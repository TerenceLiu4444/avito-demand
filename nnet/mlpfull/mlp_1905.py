# https://www.kaggle.com/lopuhin/mercari-golf-0-3875-cv-in-75-loc-1900-s
import os; os.environ['OMP_NUM_THREADS'] = '1'
from contextlib import contextmanager
from functools import partial
from operator import itemgetter
from multiprocessing.pool import ThreadPool
import time, gc
from typing import List, Dict
from sklearn import metrics

import keras as ks
import pandas as pd
import numpy as np
import tensorflow as tf
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer as Tfidf
from sklearn.pipeline import make_pipeline, make_union, Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from sklearn.metrics import mean_squared_log_error
from sklearn.model_selection import KFold
start_time = time.time()
#path = '../input/'
path = "/home/darragh/avito/data/"
path = '/Users/dhanley2/Documents/avito/data/'

@contextmanager
def timer(name):
    t0 = time.time()
    yield
    print(f'[{name}] done in {time.time() - t0:.0f} s')
    
def load_data(full):
    print('[{}] Load Train/Test'.format(time.time() - start_time))
    traindf = pd.read_csv(path + 'train.csv.zip', index_col = "item_id", \
                          parse_dates = ["activation_date"], compression = 'zip')
    traindex = traindf.index
    testdf = pd.read_csv(path + 'test.csv.zip', index_col = "item_id", \
                         parse_dates = ["activation_date"])
    testdex = testdf.index
    featusrttl = pd.read_csv(path + '../features/user_agg.csv.gz', compression = 'gzip') # created with features/make/user_actagg_1705.py
    featusrcat = pd.read_csv(path + '../features/usercat_agg.csv.gz', compression = 'gzip') # created with features/make/user_actagg_1705.py
    featusrprd = pd.read_csv(path + '../features/user_activ_period_stats.gz', compression = 'gzip') # created with features/make/user_actagg_1705.py
    y = traindf.deal_probability.copy()
    traindf.drop("deal_probability",axis=1, inplace=True)
    print('Train shape: {} Rows, {} Columns'.format(*traindf.shape))
    print('Test shape: {} Rows, {} Columns'.format(*testdf.shape))
    
    print('[{}] Create Validation Index'.format(time.time() - start_time))
    if full:
        trnidx = (traindf.activation_date<=pd.to_datetime('2017-03-28')).values
        validx = (traindf.activation_date>=pd.to_datetime('2017-03-29')).values
    else:
        trnidx = (traindf.activation_date<=pd.to_datetime('2017-03-26')).values
        validx = (traindf.activation_date>=pd.to_datetime('2017-03-27')).values
    
    print('[{}] Combine Train and Test'.format(time.time() - start_time))
    df = pd.concat([traindf,testdf],axis=0)
    df['idx'] = range(df.shape[0])
    featusrttl.rename(columns={'title': 'all_titles'}, inplace = True)
    keep = ['user_id', 'all_titles', 'user_avg_price', 'user_ad_ct']
    df = df.reset_index().merge(featusrttl[keep], on = 'user_id').set_index('item_id')
    keep = ['user_id', 'parent_category_name', 'usercat_avg_price', 'usercat_ad_ct']
    gc.collect()
    df = df.reset_index().merge(featusrcat[keep], on = ['user_id', 'parent_category_name']).set_index('item_id')
    keep = ['user_id', 'user_activ_sum']
    gc.collect()
    df = df.reset_index().merge(featusrprd[keep], on = ['user_id'], how = 'left').set_index('item_id')
    print('\nAll Data shape: {} Rows, {} Columns'.format(*df.shape))  
    
    dtrain = df.loc[traindex,:][trnidx].reset_index()
    dvalid = df.loc[traindex,:][validx].reset_index()
    dtest  = df.loc[testdex,:].reset_index()
    
    return dtrain, dvalid, dtest, y[trnidx], y[validx], traindex, testdex

def common_users(varin, col, cutoff = 3):
    dft         = pd.DataFrame(varin.values, columns = [col])
    gp          = dft[col].value_counts().reset_index().rename(columns = {'index':col, col:col+'_ct'})
    var         = dft[[col]].merge(gp, on = col, how = 'left')[col+'_ct']
    idx         = var>cutoff
    var[idx]    = (varin.values)[idx]
    var[~idx]    = ''
    return var.astype(str).values

def preprocess(mult, df: pd.DataFrame) -> pd.DataFrame:
    df['name']      = df['title'].fillna('') 
    df['text']      = (df['description'].fillna('') + ' ' + df['title'] + ' ' \
      + df['parent_category_name'].fillna('') + ' ' + df['category_name'].fillna('') \
      + df['param_1'].fillna('') + df['param_2'].fillna('') + df['param_3'].fillna(''))
    df['price_cut'] = 'pc_'+(np.log1p(df['price'])*mult).fillna(150).astype(np.int32).astype(str)
    df['user_avg_price_cut'] = 'apc_'+(np.log1p(df['user_avg_price'])*mult).fillna(10000).astype(np.int32).astype(str)
    df['usercat_avg_price_cut'] = 'capc_'+(np.log1p(df['usercat_avg_price'])*mult).fillna(10000).astype(np.int32).astype(str)
    df['user_ad_ct'] = 'uac_'+(np.log1p(df['user_ad_ct'])*mult).fillna(10000).astype(np.int32).astype(str)
    df['usercat_ad_ct'] = 'ucac_'+(np.log1p(df['usercat_ad_ct'])*mult).fillna(10000).astype(np.int32).astype(str)
    df['item_seq_number_cut'] = 'is_'+(np.log1p(df['item_seq_number'])*mult).fillna(150).astype(np.int32).astype(str)
    df['user_activ_sum'] = 'is_'+(np.log1p(df['user_activ_sum'])*mult).fillna(150).astype(np.int32).astype(str)

    df['user']    = common_users(df['user_id'], 'user_id')
    df['image_top_1'] = 'img_'+ df['image_top_1'].fillna(3100).astype(str)
    df['region']  = 'rgn_'+ df['region'].fillna('').astype(str)
    df['city']    = df['city'].fillna('').astype(str)
    df['param_1'] = 'p1_'+ df['param_1'].fillna('').astype(str)
    df['param_2'] = 'p2_'+ df['param_2'].fillna('').astype(str)
    df['param_3'] = 'p3_'+ df['param_3'].fillna('').astype(str)
    df['parent_category_name'] = 'pc1_'+ df['parent_category_name'].fillna('').astype(str)
    df['category_name'] = 'c2_'+ df['category_name'].fillna('').astype(str)
    return df[['name', 'text', 'user', 'region', 'city', 'item_seq_number_cut', 'all_titles', 'user_avg_price_cut'\
               , 'user_type', 'price_cut', 'image_top_1', 'param_1', 'param_3', 'param_3', 'user_ad_ct'\
               , 'usercat_avg_price_cut', 'usercat_ad_ct', 'user_activ_sum']] 

def on_field(f: str, *vec) -> Pipeline:
    return make_pipeline(FunctionTransformer(itemgetter(f), validate=False), *vec)

def to_records(df: pd.DataFrame) -> List[Dict]:
    return df.to_dict(orient='records')

def fit_predict(xs, y_train) -> np.ndarray:
    X_train, X_test = xs
    config = tf.ConfigProto(
        intra_op_parallelism_threads=1, use_per_session_threads=1, inter_op_parallelism_threads=1)
    with tf.Session(graph=tf.Graph(), config=config) as sess, timer('fit_predict'):
        ks.backend.set_session(sess)
        model_in = ks.Input(shape=(X_train.shape[1],), dtype='float32', sparse=True)
        out = ks.layers.Dense(192, activation='relu')(model_in)
        #out = ks.layers.Dense(512, activation='relu')(model_in)
        out = ks.layers.Dense(64, activation='relu')(out)
        out = ks.layers.Dense(64, activation='relu')(out)
        out = ks.layers.Dense(1)(out)
        model = ks.Model(model_in, out)
        model.compile(loss='mean_squared_error', optimizer=ks.optimizers.Adam(lr=1e-3))#(lr=1e-3))
        for i in range(1):
            with timer(f'epoch {i + 1}'):
                model.fit(x=X_train, y=y_train, batch_size=2**(11 + i), epochs=1, verbose=1)
        return model.predict(X_test)[:, 0]

def main(full = False):
    def vectorizerfn(szn, sza, szt):
        return make_union(
            on_field('name',       Tfidf(max_features=szn , token_pattern='\w+')), #100000
            on_field('all_titles', Tfidf(max_features=sza , token_pattern='\w+')), #100000
            #on_field('user_categories', Tfidf(max_features=10000 , token_pattern='\w+')), #100000
            on_field('text',       Tfidf(max_features=szt, token_pattern='\w+', ngram_range=(1, 2))), #100000
        on_field(['region', 'city', 'price_cut', 'item_seq_number_cut', 'image_top_1', 'user_avg_price_cut', \
                  'param_1', 'param_3', 'param_3', 'user_type', 'user', 'user_ad_ct', \
                  'usercat_avg_price_cut', 'usercat_ad_ct', 'user_activ_sum'],
                     FunctionTransformer(to_records, validate=False), DictVectorizer()),
            n_jobs=4)
    vectorizer1 = vectorizerfn(15000, 80000, 60000)
    vectorizer2 = vectorizerfn(25000, 120000, 100000)
    y_scaler = StandardScaler()
    with timer('process train'):
        train, valid, test, y_train, y_valid, trndex, tstdex = load_data(full)
        # train, valid, test, y_train, y_valid, trndex, tstdex = dtrain, dvalid, dtest, y[trnidx], y[validx], traindex, testdex
        y_train = y_train.values
        X_train1 = vectorizer1.fit_transform(preprocess(2, train.copy())).astype(np.float32)
        X_train2 = vectorizer2.fit_transform(preprocess(5, train.copy())).astype(np.float32)
        print(f'X_train1: {X_train1.shape} of {X_train1.dtype}')
        print(f'X_train2: {X_train2.shape} of {X_train2.dtype}')
        del train
    with timer('process valid'):
        X_valid1 = vectorizer1.transform(preprocess(2, valid.copy())).astype(np.float32)
        X_valid2 = vectorizer2.transform(preprocess(5, valid.copy())).astype(np.float32)
        if full:
            X_test1 = vectorizer1.transform(preprocess(2, test.copy())).astype(np.float32)
            X_test2 = vectorizer2.transform(preprocess(5, test.copy())).astype(np.float32)
    with ThreadPool(processes=4) as pool:
        if full:
            Xb_train1, Xb_train2, Xb_test1, Xb_test2 = \
                        [x.astype(np.bool).astype(np.float32) for x in [X_train1, X_train2, X_test1, X_test2]]
            xs = [[Xb_train1, Xb_test1], [X_train1, X_test1], [Xb_train2, Xb_test2], [X_train2, X_test2]]
        else:
            Xb_train1, Xb_train2, Xb_valid1, Xb_valid2 = \
                        [x.astype(np.bool).astype(np.float32) for x in [X_train1, X_train2, X_valid1, X_valid2]]
            xs = [[Xb_train1, Xb_valid1], [X_train1, X_valid1], [Xb_train2, Xb_valid2], [X_train2, X_valid2]]
        y_pred = np.mean(pool.map(partial(fit_predict, y_train=y_train), xs), axis=0)
    if not full:
        print('Valid RMSE: {:.4f}'.format(np.sqrt(metrics.mean_squared_error(y_valid.values, y_pred))) )
        return y_pred, trndex
    return y_pred, tstdex

if __name__ == '__main__':
    full = True
    y_pred, idx = main(full)
    if full: 
        mlpsub = pd.DataFrame(y_pred,columns=["deal_probability"],index=idx)
        mlpsub['deal_probability'].clip(0.0, 1.0, inplace=True) # Between 0 and 1
        mlpsub.to_csv("../sub/mlpsub_1905.csv.gz",index=True,header=True, compression = 'gzip')
        print("All done")
        lbsub = pd.read_csv("../sub/blend06.csv")
        blend_lb = lbsub.copy()
        blend_lb['deal_probability'] = mlpsub['deal_probability'].values*0.5 + lbsub['deal_probability'].values*0.5
        blend_lb.to_csv("../sub/mlpblend_1905.csv.gz",index=False,header=True, compression = 'gzip')
        print("get previous done")
        mlpsub_b4 = pd.read_csv("../sub/mlpsub_1705.csv.gz", compression = 'gzip')
        lbsub = pd.read_csv("../sub/blend06.csv")
        blend_lb = lbsub.copy()
        blend_lb['deal_probability'] = mlpsub['deal_probability'].values*0.25 + \
                                        mlpsub_b4['deal_probability'].values*0.25 + \
                                            lbsub['deal_probability'].values*0.5
        blend_lb.to_csv("../sub/mlpblend_1905.csv.gz",index=False,header=True, compression = 'gzip')

        
# Valid RMSE: 0.2166 ... all_titles bigram 60K
# Valid RMSE: 0.2165 ... avg price per user
# Valid RMSE: 0.2163 ... user Ad ct
# Valid RMSE: 0.2162 ... Parent category average price, count
# Valid RMSE: 0.2160 ... bag different models
# Valid RMSE: 0.2160 ... bag different models
# Valid RMSE: 0.2159 ... adding period sum