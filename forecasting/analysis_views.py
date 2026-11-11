"""Admin-only catalog, asynchronous analysis, and attributed expert revisions."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import IsAdmin
from accounts.throttles import ExpensiveReadRateThrottle, ForecastRefreshRateThrottle
from inventory.models import Category, Product
from .analysis_workspace import catalog
from .analysis_jobs import enqueue_analysis
from .hierarchy import start
from .methods import HORIZONS, METHODS, normalize_parameters
from .models import ForecastAnalysisRun, QualitativeForecast, QualitativeForecastRevision
from .provisional import digest
from .refresh_jobs import lookup, payload


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self,data):
        if not isinstance(data,dict) or set(data)-set(self.fields):
            raise ValidationError('Unexpected request fields.')
        return super().to_internal_value(data)


class ConfigurationSerializer(StrictSerializer):
    period=serializers.ChoiceField(choices=['daily','weekly','monthly'])
    metric=serializers.ChoiceField(choices=['demand','revenue'])
    segment=serializers.ChoiceField(choices=['combined','regular','feeding'])
    scope=serializers.ChoiceField(choices=['overall','category','item'])
    scope_id=serializers.IntegerField(min_value=1,allow_null=True,default=None)
    unit=serializers.CharField(max_length=64,allow_blank=True,default='unit')
    horizon_category=serializers.ChoiceField(choices=['short','medium','long'])
    horizon=serializers.IntegerField(min_value=2,max_value=36)
    mode=serializers.ChoiceField(choices=['inspect','automatic','manual'],default='automatic')
    method=serializers.CharField(allow_null=True,default=None)
    parameters=serializers.JSONField(default=dict)
    training_start=serializers.DateField(allow_null=True,default=None)
    training_end=serializers.DateField(allow_null=True,default=None)

    def validate(self,attrs):
        if attrs['horizon'] not in HORIZONS[attrs['horizon_category']].get(attrs['period'],[]):
            raise ValidationError('This horizon/aggregation combination is not supported.')
        if attrs['scope']=='item':
            product=Product.objects.filter(pk=attrs['scope_id']).first()
            if not product: raise ValidationError('Select an existing inventory product.')
            attrs['unit']=product.unit.strip() or 'unit'
        elif attrs['scope']=='category' and not Category.objects.filter(pk=attrs['scope_id']).exists():
            raise ValidationError('Select an existing inventory category.')
        elif attrs['scope']=='overall' and attrs['scope_id'] is not None:
            raise ValidationError('Overall scope does not accept an item/category ID.')
        if attrs['metric']=='revenue': attrs['unit']='PHP'
        elif attrs['scope']!='item' and not Product.objects.filter(unit=attrs['unit']).exists():
            raise ValidationError('Select an existing native quantity unit.')
        if attrs['training_start'] and attrs['training_end'] and attrs['training_start']>attrs['training_end']:
            raise ValidationError('Training end must be on or after training start.')
        for key in ('training_start','training_end'):
            value=attrs[key]
            if value and (value.year<2000 or value>=timezone.localdate()):
                raise ValidationError('Training dates must be completed historical dates from year 2000 onward.')
            attrs[key]=value.isoformat() if value else None
        if attrs['mode']=='manual':
            try: attrs['parameters']=normalize_parameters(attrs['method'],attrs['parameters'])
            except ValueError as error: raise ValidationError(str(error))
        elif attrs['method'] is not None or attrs['parameters']:
            raise ValidationError('Automatic/inspection mode does not accept manual method parameters.')
        return attrs


class CatalogView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]
    def get(self,request): return Response(catalog())


def run_payload(run):
    job=lookup(run.job_id)
    valid=job.status=='succeeded' and run.checksum and digest(run.findings)==run.checksum
    return {'id':run.pk,'job':payload(job),'configuration':run.configuration,'created_at':run.created_at,
        'engine_version':run.engine_version,'source_revision':run.source_revision,
        'findings':run.findings if valid else None,
        'integrity': 'verified' if valid else 'pending' if job.status in ('queued','running') else 'unavailable'}


class AnalysisRunsView(APIView):
    permission_classes=[IsAdmin]
    def get_throttles(self): return [ForecastRefreshRateThrottle() if self.request.method=='POST' else ExpensiveReadRateThrottle()]
    def get(self,request,run_id=None):
        if run_id is not None:
            run=get_object_or_404(ForecastAnalysisRun.objects.select_related('job'),pk=run_id)
            return Response(run_payload(run))
        return Response([{'id':r.pk,'configuration':r.configuration,'status':r.job.status,'created_at':r.created_at}
            for r in ForecastAnalysisRun.objects.select_related('job')[:20]])
    def post(self,request,run_id=None):
        if run_id is not None: return Response({'detail':'Create analyses at /api/forecasting/analysis/runs/.'},status=405)
        query=ConfigurationSerializer(data=request.data);query.is_valid(raise_exception=True)
        run=enqueue_analysis(query.validated_data,request.user)
        if run is None: return Response({'detail':'Forecast maintenance is already running. Wait for it to finish.'},status=409)
        response=Response(run_payload(run),status=202)
        response['Location']=f'/api/forecasting/analysis/runs/{run.pk}/'
        return response


class ExpertPointSerializer(StrictSerializer):
    date=serializers.DateField()
    value=serializers.DecimalField(max_digits=20,decimal_places=4)


class ExpertSerializer(StrictSerializer):
    product_id=serializers.IntegerField(min_value=1)
    period=serializers.ChoiceField(choices=['daily','weekly','monthly'])
    segment=serializers.ChoiceField(choices=['combined','regular','feeding'])
    kind=serializers.ChoiceField(choices=['absolute','delta','percent'])
    base_run_id=serializers.IntegerField(min_value=1,allow_null=True,default=None)
    targets=ExpertPointSerializer(many=True,min_length=1,max_length=36)
    reason=serializers.CharField(max_length=4000)
    assumptions=serializers.CharField(max_length=4000,allow_blank=True,default='')
    expected_change=serializers.CharField(max_length=4000,allow_blank=True,default='')
    event=serializers.CharField(max_length=255,allow_blank=True,default='')
    expected_version=serializers.IntegerField(min_value=1,required=False)

    def validate(self,attrs):
        product=Product.objects.filter(pk=attrs['product_id']).first()
        if not product or not product.is_active: raise ValidationError('Select an active inventory product for a new expert estimate.')
        if len({p['date'] for p in attrs['targets']})!=len(attrs['targets']): raise ValidationError('Each target period must be unique.')
        baseline={}
        if attrs['base_run_id']:
            run=ForecastAnalysisRun.objects.select_related('job').filter(pk=attrs['base_run_id'],job__status='succeeded').first()
            if not run or not run.checksum or digest(run.findings)!=run.checksum: raise ValidationError('Select a verified saved statistical run.')
            config=run.configuration
            if (config['scope'],config['scope_id'],config['metric'],config['period'],config['segment'])!=('item',product.pk,'demand',attrs['period'],attrs['segment']) or config['unit']!=(product.unit.strip() or 'unit'):
                raise ValidationError('The statistical baseline must match this product, demand metric, period, and segment.')
            baseline={p['date']:p['value'] for p in run.findings['series']['forecast']}
        if attrs['kind']!='absolute' and not baseline: raise ValidationError('Delta/percentage adjustments require a matching saved statistical baseline.')
        points=[]
        for point in attrs['targets']:
            day=point['date']; value=point['value']
            if day!=start(day,attrs['period']) or day<start(timezone.localdate(),attrs['period']):
                raise ValidationError('Expert targets must begin on a current/future aggregation boundary.')
            if day.year>timezone.localdate().year+3: raise ValidationError('Expert targets may extend at most three calendar years ahead.')
            original=baseline.get(day.isoformat())
            if attrs['kind']!='absolute' and original is None: raise ValidationError('No statistical estimate exists for an adjusted target.')
            result=value if attrs['kind']=='absolute' else Decimal(str(original))+value if attrs['kind']=='delta' else Decimal(str(original))*(1+value/100)
            if result<0 or result>Decimal('9999999999999999'): raise ValidationError('Adjusted demand must be finite, nonnegative, and within the supported range.')
            points.append({'date':day.isoformat(),'input':str(value),'statistical_value':original,'value':float(result.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))})
        attrs['targets']=points
        return attrs


def expert_payload(record):
    revisions=[{'version':r.version,'base_run_id':r.base_run_id,'kind':r.kind,'targets':r.targets,
        'reason':r.reason,'assumptions':r.assumptions,'expected_change':r.expected_change,'event':r.event,
        'created_by':{'id':r.created_by_id,'username':r.created_by.username},'created_at':r.created_at}
        for r in record.revisions.select_related('created_by').all()]
    return {'id':record.pk,'product_id':record.product_id,'period':record.period,'segment':record.segment,
        'unit':record.product.unit,'created_at':record.created_at,'updated_at':record.updated_at,
        'revisions':revisions,'basis':'User-supplied expert demand. Separate from statistical results; no forecast accuracy is claimed.'}


class ExpertForecastView(APIView):
    permission_classes=[IsAdmin]
    throttle_classes=[ExpensiveReadRateThrottle]
    def get(self,request,record_id=None):
        if record_id: return Response(expert_payload(get_object_or_404(QualitativeForecast.objects.select_related('product'),pk=record_id)))
        query=QualitativeForecast.objects.select_related('product').all()
        for param,field in [('product_id','product_id'),('period','period'),('segment','segment')]:
            value=request.query_params.get(param)
            if value:
                if param=='product_id' and not value.isdecimal(): raise ValidationError('product_id must be an integer.')
                query=query.filter(**{field:value})
        return Response([expert_payload(record) for record in query[:50]])
    def post(self,request,record_id=None):
        query=ExpertSerializer(data=request.data);query.is_valid(raise_exception=True)
        values=query.validated_data
        with transaction.atomic():
            if record_id:
                record=get_object_or_404(QualitativeForecast.objects.select_for_update(),pk=record_id)
                if (record.product_id,record.period,record.segment)!=(values['product_id'],values['period'],values['segment']): raise ValidationError('A revision cannot change its product, aggregation, or segment.')
                latest=record.revisions.order_by('-version').first()
                if values.get('expected_version')!=latest.version: return Response({'detail':'This expert estimate has a newer revision. Reload before editing.'},status=409)
                version=latest.version+1
                record.save(update_fields=['updated_at'])
            else:
                if 'expected_version' in values: raise ValidationError('New expert estimates do not have an expected version.')
                record=QualitativeForecast.objects.create(product_id=values['product_id'],period=values['period'],segment=values['segment'],created_by=request.user)
                version=1
            QualitativeForecastRevision.objects.create(forecast=record,version=version,created_by=request.user,
                **{k:values[k] for k in ('kind','targets','reason','assumptions','expected_change','event')},base_run_id=values['base_run_id'])
        return Response(expert_payload(record),status=201)
