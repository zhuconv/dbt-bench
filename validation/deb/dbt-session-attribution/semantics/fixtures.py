"""Small declared input, designed without querying the native grader."""
from datetime import datetime,timedelta


def fixture():
    sessions=[];sales=[];anchor=datetime(2024,6,20,12)
    def touch(customer,days=1,**kw):
        row={'session_id':f's{len(sessions):03d}','customer_id':customer,'session_start':str(anchor-timedelta(days=days)),
             'referrer':'','utm_source':None,'utm_medium':None,'device_type':'desktop','duration_seconds':5,
             'page_views':2,'is_converted':False,'order_id':None}
        row.update(kw);sessions.append(row);return row
    def conversion(customer,amount='100.00'):
        order='order-'+customer;touch(customer,0,is_converted=True,order_id=order)
        # Two line items deliberately require SUM, not MAX or one arbitrary row.
        from decimal import Decimal
        half=Decimal(amount)/2
        sales.extend([{'order_id':order,'total_amount':str(half)} for _ in range(2)])
    # Priority overlaps and each label, including case-insensitive classification.
    for i,fields in enumerate([
        {'utm_medium':'CpC','referrer':'FACEBOOK-google-mail','utm_source':'ad'},
        {'utm_medium':'PPC','referrer':'bing'},
        {'utm_medium':'paid_social','referrer':'google'},
        {'referrer':'FACEBOOK.google','utm_source':'campaign'},
        {'referrer':'google-twitter-mail','utm_medium':'email'},
        {'referrer':'BING'}, {'referrer':'yahoo'},
        {'referrer':'facebook','utm_source':''}, {'referrer':'twitter','utm_medium':'email'},
        {'referrer':'linkedin'}, {'referrer':'instagram'},
        {'utm_medium':'EMAIL'}, {'referrer':'MAIL.example'},
        {'referrer':None}, {'referrer':''}, {'referrer':'partner.example'},
    ]):
        key='label'+str(i);touch(key,**fields);conversion(key,str(101+i))
    # Rejected low-quality and bot touches would introduce large changes if admitted.
    touch('quality',1,utm_medium='cpc');touch('quality',2,duration_seconds=4,utm_medium='email')
    touch('quality',3,page_views=1,referrer='google');touch('quality',4,device_type='BoT',referrer='twitter')
    conversion('quality','500')
    # Inclusive lower bound, strict upper bound and wrong-customer exclusion.
    touch('boundary',14,utm_medium='email');touch('boundary',14+1/86400,utm_medium='cpc')
    touch('boundary',0,referrer='google');touch('boundary',-1,referrer='twitter');conversion('boundary','300')
    touch('no-conversion',1,utm_medium='email',order_id='order-boundary',is_converted=False)
    # Two touch, middle touch, fractional-day decay, repeated channel/no bonus.
    for key,days,labels in [
        ('two',[10,1],['cpc','email']),
        ('three',[10,3.5,.5],['cpc','email',None]),
        ('repeat',[10,3.5,.5],['cpc','email','cpc']),
        ('round1',[10,1],['cpc','email']),
        ('round2',[10,1],['cpc','email']),
    ]:
        for day,label in zip(days,labels):touch(key,day,utm_medium=label)
        conversion(key,'0.03' if key.startswith('round') else '120')
    conversion('zero','1000')
    return {'sessions':sessions,'sales':sales}
