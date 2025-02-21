from functools import partial
import numpy as np
from sklearn.metrics import roc_auc_score

from joblib import Parallel,delayed
from sklearn.utils import axis0_safe_slice

from .PruningClassifier import PruningClassifier
def error(i, ensemble_proba, selected_models, target):
    ''' 
    Computes the error of the sub-ensemble including the i-th classifier.  

    Reference:

        Margineantu, D., & Dietterich, T. G. (1997). Pruning Adaptive Boosting. Proceedings of the Fourteenth International Conference on Machine Learning, 211–218. https://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.38.7017&rep=rep1&type=pdf
    '''
    iproba = ensemble_proba[i,:,:]
    sub_proba = ensemble_proba[selected_models, :, :]
    pred = 1.0 / (1 + len(sub_proba)) * (sub_proba.sum(axis=0) + iproba)
    return (pred.argmax(axis=1) != target).mean() 

def neg_auc(i, ensemble_proba, selected_models, target):
    ''' 
    Compute the (negative) roc-auc score of the sub-ensemble including the i-th classifier.  
    '''
    iproba = ensemble_proba[i,:,:]
    sub_proba = ensemble_proba[selected_models, :, :]
    pred = 1.0 / (1 + len(sub_proba)) * (sub_proba.sum(axis=0) + iproba)

    if(sub_proba.shape[1] == 2):
        pred = pred.argmax(axis=1)
        return - 1.0 * roc_auc_score(target, pred)
    else:
        return - 1.0 * roc_auc_score(target, pred, multi_class="ovr")

def complementariness(i, ensemble_proba, selected_models, target):
    '''
    Computes the complementariness of the i-th classifier wrt. to the sub-ensemble. A classifier is complementary to the sub-ensemble if it disagrees with the ensemble, but is correct (and the ensemble is wrong)

    Reference:
        Martínez-Muñoz, G., & Suárez, A. (2004). Aggregation ordering in bagging. Proceedings of the IASTED International Conference. Applied Informatics, 258–263. Retrieved from https://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.59.2035&rep=rep1&type=pdf
    '''
    iproba = ensemble_proba[i,:,:]
    sub_proba = ensemble_proba[selected_models, :, :]

    b1 = (iproba.argmax(axis=1) == target)
    b2 = (sub_proba.sum(axis=0).argmax(axis=1) != target)
    return - 1.0 * np.sum(np.logical_and(b1, b2))

def margin_distance(i, ensemble_proba, selected_models, target, p_range = [0, 0.25]):
    '''
    Computes how including the i-th classifiers into the sub-ensemble changes its prediction towards a reference vector.

    Note: The paper randomly samples p from (0, 0.25) which we also use as a default here. If you want to change these values you can use `partial` to set them  before creating a new GreedyPruningClassifier:

    ```Python
        from functools import partial
        m_function = partial(margin_distance, p_range = [0, 0.75])
        pruner = GreedyPruningClassifier(n_estimators = 10, metric = m_function, n_jobs = 8)
    ```

    Reference:
        Martínez-Muñoz, G., & Suárez, A. (2004). Aggregation ordering in bagging. Proceedings of the IASTED International Conference. Applied Informatics, 258–263. Retrieved from https://citeseerx.ist.psu.edu/viewdoc/download?doi=10.1.1.59.2035&rep=rep1&type=pdf
    '''
    iproba = ensemble_proba[i,:,:]
    sub_proba = ensemble_proba[selected_models, :, :]
    c_refs = []

    for sub in sub_proba:
        c_refs.append( 2 * (sub.argmax(axis=1) == target) - 1.0)
    
    c_refs.append(2 * (iproba.argmax(axis=1) == target) - 1.0)
    c_refs = np.mean(c_refs)

    p = np.random.uniform(p_range[0], p_range[1], len(target))
    return np.mean((p - c_refs)**2)

def drep(i, ensemble_proba, selected_models, target, rho = 0.25):
    '''
    A multi-class version of a PAC-style bound which includes the diversity of the sub-ensemble. This basically counts the number of different predictions between the i-th classifier and the sub-ensemble.
    
    Note: The paper suggest to use \( \\rho \\in \\{0.2, 0.25, \dots, 0.5 \\} \). Our default is 0.25 here because it just works well. 

    Reference:
        Li, N., Yu, Y., & Zhou, Z.-H. (2012). Diversity Regularized Ensemble Pruning. In P. A. Flach, T. De Bie, & N. Cristianini (Eds.), Machine Learning and Knowledge Discovery in Databases (pp. 330–345). Berlin, Heidelberg: Springer Berlin Heidelberg. https://link.springer.com/content/pdf/10.1007%2F978-3-642-33460-3.pdf
    '''
    
    if len(selected_models) == 0:
        iproba = ensemble_proba[i,:,:].argmax(axis=1)
        return (iproba != target).mean()
    else:
        sub_ensemble = ensemble_proba[selected_models, :, :]
        sproba = sub_ensemble.mean(axis=0).argmax(axis=1)
        diffs = []
        for j in range(ensemble_proba.shape[0]):
            if j not in selected_models:
                jproba = ensemble_proba[j,:,:].argmax(axis=1)
                d = (jproba == sproba).sum()
                diffs.append( (j,d) )

        gamma = sorted(diffs, key = lambda e: e[1], reverse=False)
        K = int(np.ceil(rho * len(gamma)))
        topidx = [idx for idx in gamma[:K]]

        if i in topidx:
            iproba = ensemble_proba[i,:,:].argmax(axis=1)
            pred = 1.0 / (1 + len(sub_ensemble)) * (sub_ensemble.sum(axis=0) + iproba)
            return (pred.argmax(axis=1) != target).mean() 
        else:
            return np.inf

class GreedyPruningClassifier(PruningClassifier):
    ''' Greedy / Ordering-based pruning. 
    
    Greedy or ordering-based methods order the estimators in the ensemble according to their performance. In contrast to ranking-based pruning however they also consider the already selected sub-ensemble for selecting the next classifier. They start with the empty ensemble and then greedily select in each round that classifier which minimizes a loss function the most:

    $$
    \\arg\\min_{h} L\\left(\\frac{K-1}{K} f(x) + \\frac{1}{K} \\cdot h(x), y\\right)
    $$

    where f is the already selected ensemble with K-1 members and h is the newly selected member. In this sense, this selection re-order the classifiers in the ensemble and hence sometimes the name ordering-based pruning is used. In this implementation a loss function receives 4 parameters:

    - `i` (int): The classifier which should be rated
    - `ensemble_proba` (A (M, N, C) matrix ): All N predictions of all M classifier in the entire ensemble for all C classes
    - `selected_models` (list of ints): All models which are selected so far
    - `target` (list / array): A list / array of class targets.

    A simple loss function which minimizes the overall sub-ensembles error would be

    ```Python
        def error(i, ensemble_proba, selected_models, target):
            iproba = ensemble_proba[i,:,:]
            sub_proba = ensemble_proba[selected_models, :, :]
            pred = 1.0 / (1 + len(sub_proba)) * (sub_proba.sum(axis=0) + iproba)
            return (pred.argmax(axis=1) != target).mean() 
    ```

    Attributes
    ----------
    n_estimators : int, default is 5
        The number of estimators which should be selected.
    metric : function, default is error
        A function that assigns a score (smaller is better) to each classifier which is then used for selecting the next classifier in each round
    n_jobs : int, default is 8
        The number of threads used for computing the individual metrics for each classifier.
    '''
    def __init__(self, n_estimators = 5, metric = error, n_jobs = 8, **kwargs):
        """
        Creates a new GreedyPruningClassifier.

        Parameters
        ----------

        n_estimators : int, default is 5
            The number of estimators which should be selected.
        metric : function, default is error
            A function that assigns a score (smaller is better) to each classifier which is then used for selecting the next classifier in each round
        n_jobs : int, default is 8
            The number of threads used for computing the individual metrics for each classifier.
        kwargs : 
            Any additional kwargs are directly supplied to the metric function via a partial
        """
        super().__init__()

        assert metric is not None, "You did not provide a valid metric for model selection. Please do so"
        self.n_estimators = n_estimators
        self.n_jobs = n_jobs

        if len(kwargs) > 0:
            self.metric = partial(metric, **kwargs)
        else:
            self.metric = metric

    # I assume that Parallel keeps the order of evaluations regardless of its backend (see eg. https://stackoverflow.com/questions/56659294/does-joblib-parallel-keep-the-original-order-of-data-passed)
    # But for safty measures we also return the index of the current model
    def _metric(self, i, ensemble_proba, selected_models, target):
        return (i, self.metric(i, ensemble_proba, selected_models, target))

    def prune_(self, proba, target, data = None):
        n_received = len(proba)
        if self.n_estimators >= n_received:
            return range(0, n_received), [1.0 / n_received for _ in range(n_received)]

        not_seleced_models = list(range(n_received))
        selected_models = [ ]

        for _ in range(self.n_estimators):
            scores = Parallel(n_jobs=self.n_jobs, backend="threading")(
                delayed(self._metric) ( i, proba, selected_models, target) for i in not_seleced_models
            )

            best_model, _ = min(scores, key = lambda e: e[1])
            not_seleced_models.remove(best_model)
            selected_models.append(best_model)

        return selected_models, [1.0 / len(selected_models) for _ in selected_models]