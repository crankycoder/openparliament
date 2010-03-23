from django.db import models, backend
from django.contrib import databrowse
from django.conf import settings
import urllib, urllib2, re, os.path, gzip
from BeautifulSoup import BeautifulSoup
from parliament.core import parsetools
from decimal import Decimal

POL_LOOKUP_URL = 'http://webinfo.parl.gc.ca/MembersOfParliament/ProfileMP.aspx?Key=%d&Language=E'

class InternalXref(models.Model):
    text_value = models.CharField(max_length=50, blank=True)
    int_value = models.IntegerField(blank=True, null=True)
    target_id = models.IntegerField()
    schema = models.CharField(max_length=15)

class PartyManager(models.Manager):
    
    def getByName(self, name):
        x = InternalXref.objects.filter(schema='party_names', text_value=name.strip().lower())
        if len(x) == 0:
            raise Party.DoesNotExist()
        elif len(x) > 1:
            raise Exception("More than one party matched %s" % name.strip().lower())
        else:
            return self.get_query_set().get(pk=x[0].target_id)
            
class Party(models.Model):
    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=10, blank=True)
    
    objects = PartyManager()
    
    class Meta:
        verbose_name_plural = 'Parties'
        ordering = ('name',)
        
    def __init__(self, *args, **kwargs):
        """ If we're creating a new object, set a flag to save the name to the alternate-names table. """
        super(Party, self).__init__(*args, **kwargs)
        self._saveAlternate = True

    def save(self):
        super(Party, self).save()
        if hasattr(self, '_saveAlternate') and self._saveAlternate:
            self.addAlternateName(self.name)

    def delete(self):
        InternalXref.objects.filter(schema='party_names', target_id=self.id).delete()
        super(Party, self).delete()

    def addAlternateName(self, name):
        name = name.strip().lower()
        # check if exists
        x = InternalXref.objects.filter(schema='party_names', text_value=name)
        if len(x) == 0:
            InternalXref(schema='party_names', target_id=self.id, text_value=name).save()
        else:
            if x[0].target_id != self.id:
                raise Exception("Name %s already points to a different party" % name.strip().lower())
                
    def __unicode__(self):
        return self.name

class Person(models.Model):
    
    name = models.CharField(max_length=100)
    name_given = models.CharField("Given name", max_length=50, blank=True)
    name_family = models.CharField("Family name", max_length=50, blank=True)

    def __unicode__(self):
        return self.name
    
    class Meta:
        abstract = True
        ordering = ('name',)

class PoliticianManager(models.Manager):
    
    def filterByName(self, name):
        return [self.get_query_set().get(pk=x.target_id) for x in InternalXref.objects.filter(schema='pol_names', text_value=parsetools.normalizeName(name))]
    
    def getByName(self, name, session=None, riding=None, election=None, party=None):
        """ Return a Politician by name. Uses a bunch of methods to try and deal with variations in names.
        If given any of a session, riding, election, or party, returns only politicians who match.
        If given both session and riding, tries to match the name more laxly. """
        
        # Alternate names for a pol are in the InternalXref table. Assemble a list of possibilities
        poss = InternalXref.objects.filter(schema='pol_names', text_value=parsetools.normalizeName(name))
        if len(poss) >= 1:
            # We have one or more results
            if session or riding or party:
                # We've been given extra criteria -- see if they match
                result = None
                for p in poss:
                    # For each possibility, assemble a list of matching Members
                    members = ElectedMember.objects.filter(politician=p.target_id)
                    if riding: members = members.filter(riding=riding)
                    if session: members = members.filter(session=session)
                    if party: members = members.filter(party=party)
                    if len(members) >= 1:
                        if result: # we found another match on a previous journey through the loop
                            # can't disambiguate, raise exception
                            raise Politician.MultipleObjectsReturned()
                        # We match! Save the result.
                        result = members[0].politician
                if result:
                    return result
            elif election:
                raise Exception("Election not implemented yet in Politician getByName")
            else:
                # No extra criteria -- return what we got (or die if we can't disambiguate)
                if len(poss) > 1:
                    raise Politician.MultipleObjectsReturned()
                else:
                    return self.get_query_set().get(pk=poss[0].target_id)
        if session and riding:
            # We couldn't find the pol, but we have the session and riding, so let's give this one more shot
            # We'll try matching only on last name
            match = re.search(r'\s([A-Z][\w-]+)$', name.strip()) # very naive lastname matching
            if match:
                lastname = match.group(1)
                pols = self.get_query_set().filter(name_family=lastname, electedmember__session=session, electedmember__riding=riding).distinct()
                if len(pols) > 1:
                    raise Exception("DATA ERROR: There appear to be two politicians with the same last name elected to the same riding from the same session... %s %s %s" % (lastname, session, riding))
                elif len(pols) == 1:
                    # yes!
                    pol = pols[0]
                    pol.addAlternateName(name) # save the name we were given as an alternate
                    return pol
        raise Politician.DoesNotExist("Could not find politician named %s" % name)
    
    def getByParlID(self, parlid, session=None, election=None, lookOnline=True):
        try:
            x = InternalXref.objects.get(schema='pol_parlid', int_value=parlid)
            polid = x.target_id
        except InternalXref.DoesNotExist:
            if not lookOnline:
                return None
            print "Unknown parlid %d... " % parlid,
            soup = BeautifulSoup(urllib2.urlopen(POL_LOOKUP_URL % parlid))
            if soup.find('table', id='MasterPage_BodyContent_PageContent_PageContent_pnlError'):
                print "Error page for parlid %d" % parlid
                return None
            polname = soup.find('span', id='MasterPage_MasterPage_BodyContent_PageContent_Content_TombstoneContent_TombstoneContent_ucHeaderMP_lblMPNameData').string
            polriding = soup.find('a', id='MasterPage_MasterPage_BodyContent_PageContent_Content_TombstoneContent_TombstoneContent_ucHeaderMP_hlConstituencyProfile').string
            parlinfolink = soup.find('a', id='MasterPage_MasterPage_BodyContent_PageContent_Content_TombstoneContent_TombstoneContent_ucHeaderMP_hlFederalExperience')
                        
            try:
                riding = Riding.objects.getByName(polriding)
            except Riding.DoesNotExist:
                raise Politician.DoesNotExist("Couldn't find riding %s" % polriding)
            if session:
                pol = self.getByName(name=polname, session=session, riding=riding)
            else:
                pol = self.getByName(name=polname, riding=riding)
            print "found %s." % pol
            pol.saveParlID(parlid)
            polid = pol.id
            if parlinfolink:
                match = re.search(r'Item=(.+?)&', parlinfolink['href'])
                pol.saveParlinfoID(match.group(1))
        #if session:
        #    return ElectedMember.objects.get(session=session, member=polid)
        #elif election:
        #    return Candidacy.objects.get(election=election, candidate=polid)
        #else:
        return self.get_query_set().get(pk=polid)

class Politician(Person):
    GENDER_CHOICES = (
        ('M', 'Male'),
        ('F', 'Female'),
    )

    dob = models.DateField(blank=True, null=True)
    site = models.URLField(blank=True, verify_exists=False)
    parlpage = models.URLField(blank=True, verify_exists=False)
    gender = models.CharField(max_length=1, blank=True, choices=GENDER_CHOICES)
    
    objects = PoliticianManager()
    
    def __init__(self, *args, **kwargs):
        """ If we're creating a new object, set a flag to save the name to the alternate-names table. """
        super(Politician, self).__init__(*args, **kwargs)
        self._saveAlternate = True
        
    def save(self):
        super(Politician, self).save()
        if hasattr(self, '_saveAlternate') and self._saveAlternate:
            self.addAlternateName(self.name)
    
    def delete(self):
        InternalXref.objects.filter(schema='pol_parlid', target_id=self.id).delete()
        InternalXref.objects.filter(schema='pol_names', target_id=self.id).delete()
        super(Politician, self).delete()

    def saveParlID(self, parlid):
        if InternalXref.objects.filter(schema='pol_parlid', int_value=parlid).count() > 0:
            raise Exception("ParlID %d already in use" % parlid)
        InternalXref(schema='pol_parlid', int_value=parlid, target_id=self.id).save()
        
    def saveParlinfoID(self, parlinfoid):
        InternalXref.objects.get_or_create(schema='pol_parlinfoid', text_value=parlinfoid, target_id=self.id)
        
    def addAlternateName(self, name):
        name = parsetools.normalizeName(name)
        # check if exists
        if InternalXref.objects.filter(schema='pol_names', target_id=self.id, text_value=name).count() == 0:
            InternalXref(schema='pol_names', target_id=self.id, text_value=name).save()

        
class SessionManager(models.Manager):
    def current(self):
        return self.get_query_set().order_by('-start')[0]
class Session(models.Model):
    
    name = models.CharField(max_length=100)
    start = models.DateField()
    end = models.DateField(blank=True, null=True)
    parliamentnum = models.IntegerField(blank=True, null=True)
    sessnum = models.IntegerField(blank=True, null=True)

    objects = SessionManager()
    
    class Meta:
        ordering = ('-start',)

    def __unicode__(self):
        return self.name
    
class RidingManager(models.Manager):
    
    # FIXME: This should really be in the database, not the model
    FIX_RIDING = {
        'richmond-arthabasca' : 'richmond-arthabaska',
        'richemond-arthabaska' : 'richmond-arthabaska',
        'battle-river' : 'westlock-st-paul',
        'vancouver-est': 'vancouver-east',
        'calgary-ouest': 'calgary-west',
        'kitchener-wilmot-wellesley-woolwich': 'kitchener-conestoga',
        'carleton-orleans': 'ottawa-orleans',
        'frazer-valley-west': 'fraser-valley-west',
        'laval-ouest': 'laval-west',
        'medecine-hat': 'medicine-hat',
        'lac-st-jean': 'lac-saint-jean',
        'vancouver-north': 'north-vancouver',
        'laval-est': 'laval-east',
        'ottawa-ouest-nepean': 'ottawa-west-nepean',
        'cap-breton-highlands-canso': 'cape-breton-highlands-canso',
        'winnipeg-centre-sud': 'winnipeg-south-centre',
        'renfrew-nippissing-pembroke': 'renfrew-nipissing-pembroke',
        'the-battleford-meadow-lake': 'the-battlefords-meadow-lake',
        'esquimalt-de-fuca': 'esquimalt-juan-de-fuca',
        'sint-hubert': 'saint-hubert',
    }
    
    def getByName(self, name):
        slug = parsetools.slugify(name)
        if slug in RidingManager.FIX_RIDING:
            slug = RidingManager.FIX_RIDING[slug]
        return self.get_query_set().get(slug=slug)

class Riding(models.Model):
    name = models.CharField(max_length=60)
    province = models.CharField(max_length=2)
    slug = models.CharField(max_length=60, unique=True)
    edid = models.IntegerField(blank=True, null=True)
    
    objects = RidingManager()
        
    def save(self):
        if not self.slug:
            self.slug = parsetools.slugify(self.name)
        super(Riding, self).save()

    def __unicode__(self):
        return "%s (%s)" % (self.name, self.province)
    
class ElectedMember(models.Model):
    session = models.ForeignKey(Session)
    politician = models.ForeignKey(Politician)
    riding = models.ForeignKey(Riding)
    party = models.ForeignKey(Party)
    
    def __unicode__ (self):
        return u"%s (%s) was the member from %s during the %s" % (self.politician, self.party, self.riding, self.session)

