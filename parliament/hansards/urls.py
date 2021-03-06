from django.conf.urls import patterns, include, url

urlpatterns = patterns('parliament.hansards.views',
    url(r'^$', 'index', name='debates'),
    (r'^(?P<year>\d{4})/$', 'by_year'),
    (r'^(?P<year>\d{4})/(?P<month>\d{1,2})/', include(patterns('parliament.hansards.views',
        (r'^$', 'by_month'),
        url(r'^(?P<day>\d{1,2})/$', 'hansard', name='debate'),
        url(r'^(?P<day>\d{1,2})/(?P<slug>[a-zA-Z0-9-]+)/$', 'hansard_statement', name="debate"),
        url(r'^(?P<day>\d{1,2})/(?P<slug>[a-zA-Z0-9-]+)/only/$',
            'debate_permalink', name="hansard_statement_only"),
    ))),
    (r'^(?P<document_id>\d+)/local/(?P<language>en|fr)/$', 'document_cache'),
)