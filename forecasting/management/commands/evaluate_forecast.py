import csv,json
from dataclasses import replace
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.db import connection
from forecasting.contract import options as configured_options,LIMITATIONS
from forecasting.jobs import refresh


class Command(BaseCommand):
    help='Compare fixed 100k and dynamic cutoff SARIMA + rolling bulk risk, offline.'
    def add_arguments(self,parser):
        parser.add_argument('--scope',default='real')
        parser.add_argument('--all',action='store_true')
        parser.add_argument('--output', type=Path, help='Optional private directory for evaluation exports; no files are exported by default.')
        parser.add_argument('--windows',type=int,nargs='+')
        parser.add_argument('--multiplier',type=float)
        parser.add_argument('--min-positive-days',type=int)

    def handle(self,*args,**values):
        changes={key:values[key] for key in ('multiplier','min_positive_days') if values[key] is not None}
        if values['windows']:changes['windows']=tuple(values['windows'])
        try:options=replace(configured_options(),**changes)
        except ValueError as exc:raise CommandError(str(exc)) from exc
        scopes=[values['scope']]
        if values['all']:
            with connection.cursor() as cursor:
                cursor.execute('SELECT id FROM reporting_simulationdataset ORDER BY id')
                scopes=['real']+[f'simulation:{row[0]}' for row in cursor.fetchall()]
        folder=values['output']
        if folder is not None:
            folder.mkdir(parents=True,exist_ok=True)
        summary=[];scope_configurations={}
        def progress(message):self.stdout.write(message);self.stdout.flush()
        def add(scope,target,method,score,status,shared=None,unavailable=0):
            summary.append({'scope':scope,'target':target,'method':method,
                'mape_percent':score['mape_percent'],'wape_percent':score['wape_percent'],
                'coverage_percent':score['coverage_percent'],'mae_pesos':score['mae_pesos'],
                'status':status,'shared_rows':score['rows'] if shared is None else shared,
                'unavailable_rows':unavailable,'regular_training_days':options.regular_training_days,
                'point_kind':'monetary_mean'})
        for scope in scopes:
            try:options=replace(configured_options(scope),**changes)
            except ValueError as exc:raise CommandError(str(exc)) from exc
            scope_configurations[scope]=options.values()
            try:run,updated=refresh(scope,options,progress)
            except (ValueError,TypeError) as exc:raise CommandError(str(exc)) from exc
            result=run.result
            if folder is not None:
                (folder/f'{scope.replace(":","-")}.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            for setup in result['setups'].values():
                prefix=setup['setup']
                for period,evaluation in setup['periods'].items():
                    metrics=evaluation['metrics']
                    for component in ('regular','bulk','combined'):
                        method=evaluation['next_period']['method'] if component!='bulk' else 'rolling_'+evaluation['next_period']['bulk']['weighting']
                        add(scope,f'{component}_{period}',prefix+':'+method,metrics[component],evaluation['status'],unavailable=len(evaluation['rows'])-metrics[component]['rows'])
                    for component in ('regular','combined','bulk'):
                        for baseline,comparison in metrics[f'{component}_baselines'].items():
                            add(scope,f'{component}_{period}',prefix+':'+baseline,comparison['baseline'],comparison['status'],comparison['shared_rows'],comparison['unavailable_rows'])
            message={'scope':scope,'updated':updated,'selected_setup':result['selected_setup']}
            if values['verbosity'] >= 2:
                message.update(selection=result['selection'],period_scores={
                    k:{p:e['metrics']['combined'] for p,e in v['periods'].items()}
                    for k,v in result['setups'].items()})
            self.stdout.write(json.dumps(message))
        if folder is None:
            return
        with (folder/'comparison.csv').open('w',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=list(summary[0]));writer.writeheader();writer.writerows(summary)
        (folder/'limitations.txt').write_text(LIMITATIONS+'\n')
        (folder/'protocol.json').write_text(json.dumps({'options':options.values(),'scope_configurations':scope_configurations,
            'selection':'Validation before earliest test period; compare dynamic windows on shared complete weekly periods by combined WAPE. Frozen profile cutoff prevents scoring targets before profile selection.',
            'regular':'Pure SARIMA conditional on regular sales; configured central or monetary-mean daily point, native marginal 80% daily intervals. Period planning uses monetary expectations.',
            'bulk':'Arithmetic or configured recency-weighted mean and observed extrema of previous complete same-type periods only; no event-date prediction.',
            'combined':'Regular daily paths scaled by 1 - expected bulk count / target days, plus expected bulk revenue. Bounds sum scaled native daily endpoints and bulk extrema; not a calibrated joint interval.',
            'baselines':'Previous 28 calendar-day regular median; exact prior weekday in origin week, repeated for long horizons; zero bulk.',
            'unknowns':'Calendar holes remain missing, incomplete periods excluded, zeros retained. Missing benchmark lags have no fallback.',
            'metrics':'Displayed metrics use weekly/monthly/yearly periods. Acceptance: combined period WAPE <=30% and at least five evaluated periods. MAPE, peso error and coverage remain visible. No daily forecasts displayed.',
            'limitations':LIMITATIONS},indent=2)+'\n')
