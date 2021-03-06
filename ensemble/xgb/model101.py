import os

from util import ensemble_util

model = ensemble_util.XGBoostModel(model_path=os.path.abspath(__file__),
                                   corr_threshold=0.9, search=20, top_n=10,
                                   eval_func=ensemble_util.xgb_sparse_greedy_f2_metric,
                                    meta_model_dir="E:\\backup\\jdfc",
                                   xgb_param={
                                       'eta': [0.05],
                                       'silent': True,  # option for logging
                                       'objective': 'binary:logistic',  # error evaluation for multiclass tasks
                                       'max_depth': range(2, 11),  # depth of the trees in the boosting process
                                       'min_child_weight': [1, 2, 3, 4, 5]
                                   },
                                   number_round=1000,
                                   )

# model.train_all_label()
# model.build_and_predict_test()
# model.get_meta_predict([1, 2, 3,  4, 5], False)
model.build_and_predict_test()