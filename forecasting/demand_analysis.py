"""Independent, versioned demand diagnostics; no inferred expert confidence.

ADI/CV² uses the Syntetos-Boylan-Croston (2005) 1.32/0.49 quadrants.
Trend/seasonal/cycle rules below are explicit application screening rules,
not probabilities or evidence of economic causation.
"""
from math import isfinite
import numpy as np
from scipy.stats import linregress
from statsmodels.tsa.seasonal import STL
from .methods import SEASONS, clean

RULES = {'version': 'demand-pattern-v1', 'minimum_observed': 12, 'minimum_positive': 4,
    'minimum_coverage': .8, 'adi_boundary': 1.32, 'positive_size_cv2_boundary': .49,
    'trend_p_value': .05, 'minimum_relative_trend_change': .2,
    'seasonal_strength': .6, 'seasonal_repetition_correlation': .3,
    'cycle_minimum_periods': 4, 'cycle_peak_power_fraction': .5,
    'cycle_minimum_repetitions': 3,
    'confidence_policy': 'No classification probability is estimated. Significance values and strength indicators are reported separately.'}


def classify(values, dates, period):
    values = clean(values)
    observed = [(i,v) for i,v in enumerate(values) if v is not None]
    known = np.array([v for _,v in observed])
    positive = known[known > 0]
    result = {'status': 'insufficient_data', 'primary': None, 'labels': [], 'confidence': None,
        'rules': RULES, 'period': period,
        'analysis_start': dates[0].isoformat() if dates else None,
        'analysis_end': dates[-1].isoformat() if dates else None,
        'observations': len(known), 'calendar_periods': len(values),
        'indicators': {'mean': float(known.mean()) if len(known) else None,
            'standard_deviation': float(known.std()) if len(known) else None,
            'coefficient_of_variation': float(known.std()/known.mean()) if len(known) and known.mean() > 0 else None,
            'occurrence_frequency': len(positive)/len(known) if len(known) else None,
            'coverage': len(known)/len(values) if values else 0,
            'adi': None, 'positive_size_cv2': None, 'trend_strength': None,
            'trend_p_value': None, 'relative_trend_change': None, 'seasonal_strength': None},
        'explanations': [], 'overlap_policy': 'ADI/CV² provides the base pattern; trending, seasonal, and cyclical are independently tested additional characteristics.'}
    if len(known) < 12 or len(positive) < 4 or len(known) < .8*len(values):
        result['explanations'] = ['Requires 12 observed periods, four positive-demand periods, and 80% observed calendar coverage. Unknown periods are not treated as zero.']
        return result
    # Missing periods make inter-demand intervals unidentified; do not compress time.
    if len(known) != len(values):
        result['explanations'] = ['Complete calendar coverage is required for ADI/CV² demand classification; missing periods make demand intervals unknown.']
        return result
    adi = len(values)/len(positive)
    cv2 = float((positive.std()/positive.mean())**2)
    base = ('stable' if cv2 < .49 else 'erratic') if adi < 1.32 else ('intermittent' if cv2 < .49 else 'lumpy')
    labels = [base]
    result['indicators'].update(adi=adi, positive_size_cv2=cv2)
    result['explanations'].append(f'Base pattern uses observed ADI {adi:.3f} and positive-size CV² {cv2:.3f}, with thresholds 1.32 and 0.49.')
    trend = linregress(np.arange(len(values)), known)
    relative = float(trend.slope*(len(values)-1)/max(float(known.mean()), 1e-12))
    if isfinite(trend.pvalue):
        result['indicators'].update(trend_strength=float(trend.rvalue**2), trend_p_value=float(trend.pvalue), relative_trend_change=relative)
        if trend.pvalue <= .05 and abs(relative) >= .2:
            labels.append('trending')
            result['explanations'].append('Calendar-index trend has p≤0.05 and an absolute fitted change of at least 20% of mean demand.')
    season = SEASONS[period]
    deseasonalized = known.copy()
    if len(values) >= 2*season and known.std() > 1e-12:
        fit = STL(known, period=season, robust=True).fit()
        denominator = float(np.var(fit.resid+fit.seasonal))
        strength = max(0., min(1., 1-float(np.var(fit.resid))/denominator)) if denominator > 1e-12 else 0.
        left = known[:-season]-fit.trend[:-season]
        right = known[season:]-fit.trend[season:]
        repeat = float(np.corrcoef(left,right)[0,1]) if min(left.std(),right.std()) > 1e-12 else 0.
        result['indicators'].update(seasonal_strength=strength, seasonal_repetition_correlation=repeat)
        deseasonalized = known-fit.seasonal
        if strength >= .6 and repeat >= .3:
            labels.append('seasonal')
            result['explanations'].append('STL seasonal strength ≥0.60 and detrended seasonal repetition correlation ≥0.30 across at least two full cycles.')
    # Only non-calendar low-frequency repetition earns the cyclical screen.
    residual = deseasonalized-(trend.intercept+trend.slope*np.arange(len(values)))
    if len(values) >= max(24, 3*season) and np.std(residual) > 1e-8:
        spectrum = abs(np.fft.rfft(residual))**2
        frequencies = np.fft.rfftfreq(len(values))
        candidates = [i for i,f in enumerate(frequencies) if f > 0 and 4 <= 1/f <= len(values)/3 and abs(1/f-season) > .2*season]
        total = float(spectrum[1:].sum())
        if candidates and total > 0:
            peak = max(candidates, key=lambda i:spectrum[i])
            fraction = float(spectrum[peak]/total)
            result['indicators'].update(cycle_period=1/float(frequencies[peak]), cycle_power_fraction=fraction)
            if fraction >= .5:
                labels.append('cyclical')
                result['explanations'].append('A non-calendar spectral peak explains ≥50% of residual spectral power over at least three repetitions; this is a screening characteristic.')
    result.update(status='classified', labels=labels,
        primary=next((label for label in ('lumpy','intermittent','erratic','seasonal','trending','cyclical','stable') if label in labels),base))
    return result
