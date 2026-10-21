"""Immutable per-task fragments with SQL-readable result projections."""
import hashlib
import json

from .storage import encode
from .payloads import unpack,pack


LEG_FIELDS=('trip','train','from','to','dep','arr','short','train_night','through_trips')


class SharedWriter:
    def __init__(self, db, run_id):
        self.run_id=run_id
        self.fingerprints={}
        self.keys={}
        self.leg_refs={}
        self.missing_parts={}
        self.pending=[]
        self.next_id=1
        for row in db.execute('SELECT part,kind,payload FROM shared_parts WHERE run_id=?',(run_id,)):
            text=unpack(row['payload'])
            digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
            self.fingerprints[row['kind'],digest]=row['part']
            if row['kind']=='missing':
                self.missing_parts[row['part']]=tuple(json.loads(text))
            self.next_id=max(self.next_id,row['part']+1)

    def intern(self, kind, key, value):
        cache_key=kind,key
        if cache_key in self.keys:
            return self.keys[cache_key]
        payload=encode(value)
        digest=hashlib.sha256(payload.encode('utf-8')).hexdigest()
        identity=self.fingerprints.get((kind,digest))
        if identity is None:
            compressed=pack(payload)
            identity=self.next_id
            self.next_id+=1
            self.fingerprints[kind,digest]=identity
            self.pending.append((self.run_id,identity,kind,compressed))
        self.keys[cache_key]=identity
        return identity

    def compact(self, payload):
        result=dict(payload)
        result['__railnomad_shared']=1
        result['legs']=[]
        for leg in payload['legs']:
            cached=self.leg_refs.get(id(leg))
            if cached is None:
                reference=self.intern('leg',id(leg),leg)
                self.leg_refs[id(leg)]=(leg,reference)
            else:
                reference=cached[1]
            result['legs'].append(dict({k:leg[k] for k in LEG_FIELDS},**{'$shared':reference}))
        result['date_evidence_variants']=[]
        for variant in payload['date_evidence_variants']:
            compact_variant=dict(variant)
            compact_variant['legs']=[]
            for evidence in variant['legs']:
                leg=payload['legs'][evidence['leg']]
                key=(leg['trip'],leg['from'],leg['to'],evidence['boarding_date'],leg['short'])
                content={k:v for k,v in evidence.items() if k!='leg'}
                reference=self.intern('leg_evidence',key,content)
                compact_variant['legs'].append({'$shared':reference,'leg':evidence['leg'],
                                               'fares':[{'seat':fare['seat']} for fare in evidence['fares']]})
            result['date_evidence_variants'].append(compact_variant)
        result['missing_evidence_keys']=[{'$shared':self.intern('missing',tuple(key),key)} for key in payload['missing_evidence_keys']]
        return result

    def flush(self, db):
        db.executemany('INSERT INTO shared_parts VALUES(?,?,?,?)',self.pending)

    def committed(self):
        self.pending.clear()
