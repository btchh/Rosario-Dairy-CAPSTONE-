"""Pure SARIMA, retaining calendar gaps and native marginal 80% intervals."""
from dataclasses import dataclass
import warnings
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

MIN_OBSERVED=180
REFIT_DAYS=14


def normalize(series):
    if not isinstance(series.index,pd.DatetimeIndex) or series.index.has_duplicates or series.index.tz is not None:
        raise ValueError('Expected unique local business dates')
    if not (series.index==series.index.normalize()).all():raise ValueError('Expected daily dates')
    series=series.sort_index().astype(float);known=series.dropna()
    if not np.isfinite(known).all() or (known<0).any():raise ValueError('Revenue must be finite and nonnegative')
    return series.reindex(pd.date_range(series.index[0],series.index[-1])) if len(series) else series

@dataclass(frozen=True)
class Candidate:
    name: str
    transform: str='level'
    order: tuple=(1,0,0)
    seasonal_order: tuple=(0,0,1,7)
    training_days: int | None=None
    point_kind: str='central'

CANDIDATES=tuple(Candidate(f'sarima_{transform}_{label}',transform,order,seasonal)
    for transform in ('level','log','sqrt') for label,order,seasonal in (
        ('stationary',(1,0,0),(0,0,1,7)),('weekly_difference',(0,1,1),(0,1,1,7)),
        ('weekly_ar',(1,0,0),(1,0,0,7))))

SIMPLE_CANDIDATES=tuple(Candidate(f'sarima_{transform}_simple_weekly_ar',transform,(0,0,0),(1,0,0,7))
    for transform in ('level','log','sqrt'))

class Predictor:
    def __init__(self,history,candidate):
        self.history=normalize(history);self.candidate=candidate;self.steps=0;self.refit_failures=0
        self.fit()

    def encode(self,values,scale):
        values=values/scale
        if self.candidate.transform=='log':return np.log1p(values)
        if self.candidate.transform=='sqrt':return np.sqrt(values)
        return values

    def decode(self,values):
        values=np.asarray(values)*self.spread+self.center
        if self.candidate.transform=='log':values=np.expm1(values)
        elif self.candidate.transform=='sqrt':values=np.maximum(values,0)**2
        values=np.maximum(values*self.scale,0)
        if not np.isfinite(values).all() or (values>=1e18).any():raise ValueError('Invalid monetary forecast')
        return values

    def fit(self):
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        if self.history.notna().sum()<30:raise ValueError('Too few regular training observations')
        training=self.history
        if self.candidate.training_days is not None:
            training=training.loc[training.index>=training.index[-1]-pd.Timedelta(days=self.candidate.training_days-1)]
        if training.notna().sum()<30:raise ValueError('Too few regular observations in training window')
        scale=max(float(training.dropna().median()),1.)
        encoded=self.encode(training,scale);center=float(encoded.mean());spread=max(float(encoded.std()),1e-6)
        c=self.candidate
        with threadpool_limits(limits=1),warnings.catch_warnings():
            warnings.simplefilter('ignore')
            fitted=SARIMAX((encoded-center)/spread,order=c.order,seasonal_order=c.seasonal_order,
                trend='c' if c.order[1]==c.seasonal_order[1]==0 else 'n',
                enforce_stationarity=True,enforce_invertibility=True).fit(disp=False,maxiter=200,cov_type='none')
        if not fitted.mle_retvals.get('converged',False):raise ValueError('SARIMA did not converge')
        self.fitted,self.scale,self.center,self.spread=fitted,scale,center,spread;self.steps=0

    def path(self,days,point_kind=None):
        if days<1:raise ValueError('Horizon must be positive')
        with threadpool_limits(limits=1):
            prediction=self.fitted.get_forecast(steps=days)
            kind=point_kind or self.candidate.point_kind
            if kind=='central':point=self.decode(prediction.predicted_mean)
            elif kind=='mean':point=self.monetary_mean(prediction.predicted_mean,prediction.var_pred_mean)
            else:raise ValueError('Unknown point forecast kind')
            intervals=np.asarray(prediction.conf_int(alpha=.2))
        return point,self.decode(intervals[:,0]),self.decode(intervals[:,1])

    def monetary_mean(self,location,variance):
        """Expectation of the same transformed Gaussian with zero clipping as decode."""
        from scipy.special import ndtr
        mu=np.asarray(location)*self.spread+self.center
        var=np.maximum(np.asarray(variance)*self.spread**2,0.)
        sigma=np.sqrt(var)
        ratio=np.divide(mu,sigma,out=np.zeros_like(mu,dtype=float),where=sigma>0)
        density=np.exp(-.5*ratio**2)/np.sqrt(2*np.pi)
        if self.candidate.transform=='sqrt':
            value=(mu**2+var)*ndtr(ratio)+mu*sigma*density
        elif self.candidate.transform=='log':
            shifted=np.divide(mu+var,sigma,out=np.zeros_like(mu,dtype=float),where=sigma>0)
            value=np.exp(mu+.5*var)*ndtr(shifted)-ndtr(ratio)
        else:value=mu*ndtr(ratio)+sigma*density
        value=np.where(sigma==0,self.decode(location)/self.scale,value)*self.scale
        if not np.isfinite(value).all() or (value>=1e18).any():raise ValueError('Invalid monetary expectation')
        return np.maximum(value,0.)

    def observe(self,stamp,actual):
        if stamp!=self.history.index[-1]+pd.Timedelta(days=1):raise ValueError('Nonconsecutive business dates')
        observation=pd.Series([actual],index=pd.date_range(stamp,periods=1))
        with threadpool_limits(limits=1):
            self.fitted=self.fitted.extend((self.encode(observation,self.scale)-self.center)/self.spread)
        self.history=pd.concat([self.history,observation]);self.history.index=pd.DatetimeIndex(self.history.index,freq='D')
        self.steps+=1


def walk(history,future,candidate):
    state=Predictor(history,candidate);rows=[]
    for stamp,actual in future.items():
        if state.steps>=REFIT_DAYS:
            try:state.fit()
            except (ValueError,np.linalg.LinAlgError):state.refit_failures+=1;state.steps=0
        point,lower,upper=state.path(1)
        rows.append({'date':str(stamp.date()),'trained_through':str(state.history.index[-1].date()),
            'predicted':round(float(point[0]),2),'lower':round(float(lower[0]),2),'upper':round(float(upper[0]),2),
            'actual':float(actual) if pd.notna(actual) else None})
        state.observe(stamp,actual)
    return rows,state
