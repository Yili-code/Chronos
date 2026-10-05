"""Durable collection requests; queued is never evidence of successful reading."""
from datetime import datetime, timedelta
from uuid import uuid4
from .assignments import aware
from .study_poll_plan import scheduled_requests


def initial_poll_state(request):
    return {
        'course_id':request.course_id, 'reason':request.reason,
        'due_at':aware(request.due_at).isoformat(), 'status':'queued',
        'claim':None, 'claimed_at':None, 'attempt_count':0}


def enqueue_poll(db, request):
    return db.mutate_study_poll(request.key,lambda old: old or initial_poll_state(request))


def enqueue_scheduled_polls(db, now):
    jobs=scheduled_requests(now)
    for request in jobs:
        enqueue_poll(db,request)
    return len(jobs)


def claim_poll(db,key,now):
    now=aware(now)
    token=uuid4().hex
    def transition(old):
        if old is None:
            raise ValueError('unknown collection job')
        if old['status']=='running':
            if now >= datetime.fromisoformat(old['claimed_at'])+timedelta(minutes=5):
                return {**old,'status':'unknown'}
            return old
        if old['status']!='queued' or datetime.fromisoformat(old['due_at'])>now:
            return old
        if old['reason']=='scheduled' and key not in {job.key for job in scheduled_requests(now)}:
            return {**old,'status':'superseded'}
        return {**old,'status':'running','claim':token,'claimed_at':now.isoformat(),
                'attempt_count':old['attempt_count']+1}
    result=db.mutate_study_poll(key,transition)
    return token if result['status']=='running' and result['claim']==token else None


def next_poll(db,now):
    """Prefer owner replies; atomically recheck candidates before returning one."""
    now=aware(now)
    candidates=sorted(db.list_study_polls(),key=lambda entry:(
        entry[1]['reason']!='progress_reply',entry[1]['due_at'],entry[0]))
    for key,state in candidates:
        if state['status'] not in ('queued','running'):
            continue
        claim=claim_poll(db,key,now)
        if claim:
            return {'key':key,'claim':claim,'course_id':state['course_id'],'reason':state['reason']}
    return None


def finish_poll(db,key,claim,outcome):
    if outcome not in ('completed','partial','reauth_required','unknown','adapter_failed'):
        raise ValueError('explicit collection outcome required')
    def transition(old):
        if old is None or old['claim']!=claim:
            raise ValueError('collection claim mismatch')
        if old['status'] not in ('running','unknown'):
            return old
        return {**old,'status':outcome}
    return db.mutate_study_poll(key,transition)
