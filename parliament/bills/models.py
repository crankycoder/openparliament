import urllib, urllib2, re

from django.db import models
from BeautifulSoup import BeautifulSoup

from parliament.core.models import Session, InternalXref, ElectedMember, Politician

CALLBACK_URL = 'http://www2.parl.gc.ca/HousePublications/GetWebOptionsCallBack.aspx?SourceSystem=PRISM&ResourceType=Document&ResourceID=%d&language=1&DisplayMode=2'
class BillManager(models.Manager):
    
    def get_by_callback_id(self, callback):
        """Given a callback ID from a link on a Hansard page, return a Bill."""
        try:
            xref = InternalXref.objects.get(schema='bill_callbackid', int_value=callback)
            return self.get_query_set().get(pk=xref.target_id)
        except InternalXref.DoesNotExist:
            pass
        callpage = urllib2.urlopen(CALLBACK_URL % callback)
        match = re.search(r"href='/HousePublications/Redirector\.aspx\?RedirectUrl=([^'>]+)'>Bill Votes</A>", callpage.read())
        if not match:
            print "Couldn't find Bill Votes link in get_by_callback_id"
            raise Bill.DoesNotExist()
        votesurl = urllib.unquote(match.group(1))
        votespage = urllib2.urlopen(votesurl)
        match = re.search(r'Parl=(\d+)&Ses=(\d+)', votesurl)
        if not match:
            raise Bill.DoesNotExist("Couldn't parse Bill Votes link")
        session = Session.objects.get(parliamentnum=match.group(1), sessnum=match.group(2))
        votesoup = BeautifulSoup(votespage.read())
        votediv = votesoup.find('div', 'VotesBill')
        match = re.search(r'([A-Z]+-\d+)\s+(.+)', votediv.string.strip())
        billnum, billname = match.group(1), match.group(2)
        try:
            bill = self.get_query_set().get(number=billnum, session=session)
        except Bill.DoesNotExist:
            bill = Bill(name=billname, number=billnum, session=session)
            bill.save()
        InternalXref(schema='bill_callbackid', int_value=callback, target_id=bill.id).save()
        return bill
        

class Bill(models.Model):
    
    name = models.CharField(max_length=500)
    number = models.CharField(max_length=10)
    number_only = models.SmallIntegerField()
    session = models.ForeignKey(Session)
    legisinfo_url = models.URLField(blank=True, null=True, verify_exists=False)
    privatemember = models.NullBooleanField()
    sponsor_member = models.ForeignKey(ElectedMember, blank=True, null=True)
    sponsor_politician= models.ForeignKey(Politician, blank=True, null=True)
    # FIXME model related bills (same bill, reintroduced)
    
    objects = BillManager()
    
    class Meta:
        ordering = ('number_only',)
    
    def __unicode__(self):
        return "%s - %s" % (self.number, self.name)
        
    @models.permalink
    def get_absolute_url(self):
        return ('parliament.bills.views.bill', [self.id])
        
    def save(self, *args, **kwargs):
        if not self.number_only:
            self.number_only = int(re.sub(r'\D', '', self.number))
        if getattr(self, 'privatemember', None) is None:
            self.privatemember = bool(self.number_only >= 200)
        super(Bill, self).save(*args, **kwargs)
        
VOTE_RESULT_CHOICES = (
    ('Y', 'Passed'), # Agreed to
    ('N', 'Failed'), # Negatives
    ('T', 'Tie'),
)
class VoteQuestion(models.Model):
    
    bill = models.ForeignKey(Bill, blank=True, null=True)
    session = models.ForeignKey(Session)
    number = models.PositiveIntegerField()
    date = models.DateField(db_index=True)
    description = models.TextField()
    result = models.CharField(max_length=1, choices=VOTE_RESULT_CHOICES)
    yea_total = models.SmallIntegerField()
    nay_total = models.SmallIntegerField()
    paired_total = models.SmallIntegerField()
    
    def __unicode__(self):
        return u"Vote #%s on %s" % (self.number, self.date)
        
    class Meta:
        ordering=('-date', '-number')

    def label_absent_members(self):
        for member in ElectedMember.objects.on_date(self.date).exclude(membervote__votequestion=self):
            MemberVote(votequestion=self, member=member, politician_id=member.politician_id, vote='A').save()
            
    @models.permalink
    def get_absolute_url(self):
        return ('parliament.bills.views.vote', [self.id])

VOTE_CHOICES = (
    ('Y', 'Yea'),
    ('N', 'Nay'),
    ('P', 'Paired'),
    ('A', "Didn't vote"),
)    
class MemberVote(models.Model):
    
    votequestion = models.ForeignKey(VoteQuestion)
    member = models.ForeignKey(ElectedMember)
    politician = models.ForeignKey(Politician)
    vote = models.CharField(max_length=1, choices=VOTE_CHOICES)