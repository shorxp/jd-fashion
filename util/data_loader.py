import math
import os
import random
import re

import cv2
import keras.backend as K
import numpy as np
import tensorflow as tf
from keras.preprocessing.image import ImageDataGenerator, Iterator, img_to_array, array_to_img
from tqdm import tqdm

import config
from util import path

training_times = 0
validation_times = 0


def check_mean_std_file(model_config, datagen):
    if not os.path.exists(model_config.image_std_file) or not os.path.exists(model_config.image_mean_file):
        datagen.calc_image_global_mean_std(model_config.train_files)
        datagen.save_image_global_mean_std(model_config.image_mean_file, model_config.image_std_file)


class KerasGenerator(ImageDataGenerator):
    def __init__(self, model_config=None, *args, **kwargs):
        super(KerasGenerator, self).__init__(*args, **kwargs)
        self.iterator = None

        if model_config is not None:
            if self.featurewise_center is not None or self.featurewise_std_normalization is not None:
                check_mean_std_file(model_config, self)
                self.load_image_global_mean_std(model_config.image_mean_file, model_config.image_std_file)

    def flow_from_files(self, img_files,
                        mode='fit',
                        target_size=(256, 256),
                        batch_size=32,
                        save_to_dir=None,
                        save_prefix='',
                        save_format='png',
                        shuffle=False, seed=None, label_position=None):
        return KerasIterator(self, img_files,
                             mode=mode,
                             target_size=target_size,
                             batch_size=batch_size,
                             shuffle=shuffle,
                             save_to_dir=save_to_dir,
                             save_prefix=save_prefix,
                             save_format=save_format,
                             seed=seed,
                             data_format=None,
                             label_position=label_position)

    def calc_image_global_mean_std(self, img_files):
        shape = (1, 3)
        mean = np.zeros(shape, dtype=np.float32)
        M2 = np.zeros(shape, dtype=np.float32)

        print('Computing mean and standard deviation on the dataset')
        for n, file in enumerate(tqdm(img_files, miniters=256), 1):
            img = cv2.imread(os.path.join(file)).astype(np.float32)
            img *= self.rescale
            mean_current = np.mean(img, axis=(0, 1)).reshape((1, 3))
            delta = mean_current - mean
            mean += delta / n
            delta2 = mean_current - mean
            M2 += delta * delta2

        self.mean = mean
        self.std = M2 / (len(img_files) - 1)

        print("Calc image mean: %s" % str([str(i) for i in self.mean]))
        print("Calc image std: %s" % str([str(i) for i in self.std]))

    def save_image_global_mean_std(self, path_mean, path_std):
        if self.mean is None or self.std is None:
            raise ValueError('Mean and Std must be computed before, fit the generator first')
        np.save(path_mean, self.mean.reshape((1, 3)))
        np.save(path_std, self.std.reshape((1, 3)))

    def load_image_global_mean_std(self, path_mean, path_std):
        self.mean = np.load(path_mean)
        self.std = np.load(path_std)
        print("Load image mean: %s" % str([str(i) for i in self.mean]))
        print("Load image std: %s" % str([str(i) for i in self.std]))


class KerasIterator(Iterator):
    def __init__(self, image_data_generator, img_files,
                 mode='fit',
                 target_size=(256, 256),
                 batch_size=32, shuffle=None, seed=None,
                 save_to_dir=None, save_prefix='', save_format='png',
                 data_format=None,
                 label_position=None):

        self.target_size = tuple(target_size)

        if data_format is None:
            self.data_format = K.image_data_format()

        if self.data_format == 'channels_last':
            self.image_shape = self.target_size + (3,)
        else:
            self.image_shape = (3,) + self.target_size

        self.image_data_generator = image_data_generator
        self.save_to_dir = save_to_dir
        self.save_prefix = save_prefix
        self.save_format = save_format
        self.img_files = img_files
        self.mode = mode
        if label_position is None:
            self.labels = np.array(get_labels(img_files), dtype=np.int8)
        else:
            self.labels = np.array(get_labels(img_files), dtype=np.int8)[:, label_position]

        # Init parent class
        super(KerasIterator, self).__init__(len(self.img_files), batch_size, shuffle, seed)

    def _get_batches_of_transformed_samples(self, index_array):
        # The transformation of images is not under thread lock
        # so it can be done in parallel
        batch_x = np.zeros((len(index_array),) + self.image_shape, dtype=K.floatx())

        # Build batch of images
        for i, j in enumerate(index_array):
            file = self.img_files[j]
            img = cv2.imread(file)
            img = cv2.resize(img, self.target_size)
            x = img_to_array(img, data_format=self.data_format)
            x = self.image_data_generator.random_transform(x)
            x = self.image_data_generator.standardize(x)
            batch_x[i] = x

        if self.save_to_dir:
            for i, j in enumerate(index_array):
                img = array_to_img(batch_x[i], self.data_format, scale=True)
                fname = '{prefix}_{index}_{hash}.{format}'.format(prefix=self.save_prefix,
                                                                  index=j,
                                                                  hash=np.random.randint(1e7),
                                                                  format=self.save_format)
                img.save(os.path.join(self.save_to_dir, fname))

        # Build batch of labels.
        if self.mode == 'fit':
            batch_y = self.labels[index_array]
            return batch_x, batch_y
        elif self.mode == 'predict':
            return batch_x
        else:
            raise ValueError('The mode should be either \'fit\' or \'predict\'')

    def next(self):
        """For python 2.x.

        # Returns
            The next batch.
        """
        with self.lock:
            index_array = next(self.index_generator)
        # The transformation of images is not under thread lock
        # so it can be done in parallel
        return self._get_batches_of_transformed_samples(index_array)


def up_sampling(files: np.ndarray, label_position):
    """
    对某一个标签进行上采样
    :param files:
    :param label_position:
    :return:
    """
    assert len(label_position) == 1

    y = np.array(get_labels(files), np.bool)[:, label_position]

    y_1 = y[y == 1]
    y_0 = y[y == 0]

    assert y_1.size != 0 and y_0.size != 0

    if y_1.size == y_0.size:
        return files

    if y_1.size > y_0.size:
        file_less = files[(y == 1)[:, 0]]
        n = y_1.size // y_0.size
        m = y_1.size % y_0.size

    elif y_1.size < y_0.size:
        file_less = files[(y == 1)[:, 0]]
        n = y_0.size // y_1.size
        m = y_0.size % y_1.size

    repeat = np.repeat(file_less, n)
    choice = np.random.choice(file_less, m)
    files = np.hstack((files, repeat, choice))
    np.random.shuffle(files)

    y = np.array(get_labels(files), np.bool)[:, label_position]
    assert (y == 1).size == (y == 0).size
    return files


def read_and_save_checkpoint(checkpoint_path, save_path):
    from tensorflow.python import pywrap_tensorflow
    reader = pywrap_tensorflow.NewCheckpointReader(checkpoint_path)
    var_to_shape_map = reader.get_variable_to_shape_map()
    with open(save_path, "w+") as f:
        for key in var_to_shape_map:
            f.write("tensor name: %s\n" % key)
            f.write(str(reader.get_tensor(key)))
            f.write("\n")


def get_max_step(model_config: config.EstimatorModelConfig, validation=False):
    total_steps = len(model_config.data_type) * config.IMAGE_NUMBER / model_config.batch_size
    if validation:
        return math.ceil(total_steps / 5)

    return math.ceil(total_steps * 4 / 5)


def _read_py_function(filename, label=None):
    image = cv2.imread(filename.decode())
    if label is None:
        return image
    else:
        return image, label


def _resize_function(image_decoded, label=None):
    image_decoded.set_shape([None, None, 3])
    image_resized = tf.image.resize_images(image_decoded, config.IMAGE_SIZE)
    if label is None:
        return image_resized
    else:
        label.set_shape([13])
        return image_resized, label


def get_labels(filenames):
    labels = []
    for i in filenames:
        label = i.split(".")[-2].split("_")[1:]
        labels.append(list(map(int, label)))
    return labels


def predict_input_fn(files, batch_size):
    data_set = tf.data.Dataset.from_tensor_slices((files,))
    data_set = data_set.map(
        lambda filename: tuple(tf.py_func(
            _read_py_function, [filename], [tf.uint8])))

    data_set = data_set.map(_resize_function)
    data_set = data_set.batch(batch_size)
    data_set = data_set.prefetch(config.PREFETCH_BUFFER_SIZE)
    iterator = data_set.make_one_shot_iterator()
    return iterator.get_next()


def data_input_fn(model_config: config.EstimatorModelConfig, validation=False):
    train_files, val_files = get_k_fold_files(model_config.k_fold_file, model_config.val_index, model_config.data_type)
    if validation:
        global validation_times
        validation_times += 1
        print("%dth validation with %d images" % (validation_times, len(val_files)))
        labels = get_labels(val_files)
        data_set = tf.data.Dataset.from_tensor_slices((val_files, labels))
    else:
        global training_times
        training_times += 1
        print("%dth training with %d images" % (training_times, len(train_files)))
        labels = get_labels(train_files)
        data_set = tf.data.Dataset.from_tensor_slices((train_files, labels))

    data_set = data_set.map(
        lambda filename, label: tuple(tf.py_func(
            _read_py_function, [filename, label], [tf.uint8, label.dtype])))

    # 此处没有添加repeated（epoch），在外部进调用train_and_evaluate函数会多次调用本函数
    data_set = data_set.map(_resize_function)
    data_set = data_set.batch(model_config.batch_size)
    data_set = data_set.prefetch(config.PREFETCH_BUFFER_SIZE)
    iterator = data_set.make_one_shot_iterator()
    features, labels = iterator.get_next()

    # train 和 validation 是在两张不同的Graph中执行的，所以tensor的名字相同
    print("labels tensor name is %s" % labels.name)
    return features, labels


def get_k_fold_files(k_fold_file, val_index, data_type: [], shuffle=True):
    train_names = []
    val_names = []
    with open(os.path.join(path.K_FOLD_TXT_PATH, k_fold_file), 'r') as f:
        for l in f.readlines():
            k, name = l.split(",")
            val_names.append(name.strip()) if int(k) is val_index else train_names.append(name.strip())

    train_files = []
    val_files = []

    for data in data_type:
        for name in train_names:
            train_files.append(os.path.join(path.get_train_data_path(data), name))
        for name in val_names:
            val_files.append(os.path.join(path.get_train_data_path(data), name))

    if shuffle:
        random.shuffle(train_files)
        random.shuffle(val_files)

    return train_files, val_files


def list_image_dir(directory, ext='jpg|jpeg|bmp|png|ppm'):
    """
    列出目录下的所有图片的路径
    :param directory:
    :param ext:
    :return:
    """
    return [os.path.join(root, f)
            for root, _, files in os.walk(directory) for f in files
            if re.match(r'([\w]+\.(?:' + ext + '))', f)]


def list_image_name(directory, ext='jpg|jpeg|bmp|png|ppm'):
    """
    列出目录下的所有图片的名称
    :param directory:
    :param ext:
    :return:
    """
    return [f for root, _, files in os.walk(directory)
            for f in files if re.match(r'([\w]+\.(?:' + ext + '))', f)]


def load_label(directory, number=None):
    """
    导入指定目录的所有图片的标签，不导入图片
    :param directory:
    :return:
    """
    names = list_image_name(directory)
    random.shuffle(names)
    if number is not None:
        names = names[:number]
    labels = []
    for name in names:
        label = name.split(".")[-2].split("_")[1:]
        labels.append(list(map(int, label)))
    return np.array(labels), np.array(names)


def divide_data(x: np.array, y: np.array, data_ratio=(0.8, 0.1, 0.1)) -> list:
    """
    将数据根据比例分为N份，x和y的对应关系保持不变
    :param x:
    :param y:
    :param data_ratio: 划分比例，要求比例之和为1，划分次数不做限制
    :return:
    """

    assert sum(data_ratio) == 1

    data_num = x.shape[0]

    pointer = []
    ratio_sum = 0
    for ratio in data_ratio:
        ratio_sum += ratio
        pointer.append(math.floor(data_num * ratio_sum))

    result = []
    for i in range(len(pointer)):
        if i is 0:
            result.append((x[:pointer[i]], y[:pointer[i]]))
        else:
            result.append((x[pointer[i - 1]:pointer[i]], y[pointer[i - 1]:pointer[i]]))

    return result


def remove_image_name_header(dir):
    names = list_image_name(dir)
    for i in names:
        headr = i.split("_")[0]
        if headr == config.DATA_TYPE_SEGMENTED or headr == config.DATA_TYPE_AUGMENTED or headr == config.DATA_TYPE_ORIGINAL:
            name_target = "_".join(i.split("_")[1:])
            os.rename(os.path.join(dir, i),
                      os.path.join(dir, name_target))


def image_repair():
    names = list_image_dir(path.ORIGINAL_TRAIN_IMAGES_PATH)
    for name in names:
        img = cv2.imread(name)
        cv2.imwrite(name, img)


if __name__ == '__main__':
    image_repair()
    # remove_image_name_header(path.ORIGINAL_TRAIN_IMAGES_PATH)
    # remove_image_name_header(path.SEGMENTED_TRAIN_IMAGES_PATH)

    # data_input_fn()
