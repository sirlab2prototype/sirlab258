import numpy as np
from ai.train.evaluate_samples import error_stats

def test_metrics_are_label_error_not_state_error():
 x=np.deg2rad(np.array([[0,1,2,0,0,0],[0,0,0,0,0,0.]]));r=error_stats(x)
 assert r['observations_over1_5deg']==1
 assert abs(r['mean_abs_deg'][2]-1)<1e-10
 assert abs(r['max_abs_deg'][2]-2)<1e-10
