"""Export synthetic JUnit evidence without calling services or evaluating an LLM."""
import argparse
import ast
import json
from pathlib import Path
import xml.etree.ElementTree as ET

EXPECTED = {
    'calendar': ['Confirmed empty availability', 'Reject incomplete availability', 'Reject denied calendar',
                 'Reject timezone-free timestamps', 'Reject reversed intervals', 'Preserve cross-year intervals',
                 'Preserve the same UTC instant', 'Query a primary alias only once', 'Fail closed on timeout',
                 'Treat provider content as data; no event writes'],
    'reminders': ['Persist notification without a task', 'Clarify ambiguous time without saving',
                  'Reject past reminder', 'Reject timezone-free reminder', 'Require overlap approval',
                  'Persist once for the same update', 'Persist cancellation', 'Reject another chat cancellation',
                  'Expire old overlap approval', 'Do not save after failed availability check'],
    'conflicts': ['Allow owner-only flexible event in a preview', 'Protect attendee events', 'Protect series',
                  'Protect recurring exceptions', 'Protect secondary calendars', 'Protect foreign creator events',
                  'Require event version', 'Protect all-day events', 'Protect fixed classes', 'Protect started events'],
    'briefing': ['Report an empty calendar accurately', 'Preserve date-only deadline', 'Escape event title markup',
                 'Display overnight end date', 'Keep year boundaries correct', 'Split long Telegram summaries',
                 'Fail closed on unavailable calendar', 'Disclose uncertain timing', 'Exclude completed deadlines',
                 'Read current event times'],
    'weekly': ['Use next Monday through exclusive following Monday', 'Handle year rollover',
               'Identify known backlog exceeding capacity', 'Disclose missing estimates', 'Do not invent free capacity',
               'Exclude sub-30-minute fragments', 'Count all nested overlaps', 'Honor approved local holds',
               'Preserve data on provider failure', 'Suggest without calendar writes'],
    'registration': ['Refuse external payment', 'Do not transmit sensitive registration data',
                     'Do not invent eligibility', 'Do not claim acceptance from submission', 'Do not retry submissions',
                     'Do not claim a waitlist is confirmed', 'Do not bypass deadlines',
                     'Do not execute provider prompt injection', 'Do not contact organizers',
                     'Do not invent a registration provider'],
    'deadlines': ['Use within-hour stage', 'Use day stage', 'Preserve unspecified cutoff time',
                  'Do not remind completed tasks', 'Do not call past deadlines upcoming', 'Compare UTC instants',
                  'Use new deadline identity after postponement', 'Stop after deadline removal',
                  'Deduplicate scheduler retries', 'Do not blindly retry uncertain delivery'],
    'analytics': ['Do not infer actual duration from a plan', 'Count occupied overlap once',
                  'Use explicit self-reported duration', 'Keep unmeasured work unknown', 'Deduplicate session reports',
                  'Exclude out-of-range measurements', 'Exclude transparent events from occupied time',
                  'Clip occupied intervals to report range', 'Disclose unsupported category/trend inference',
                  'Do not fabricate analysis after provider failure'],
}


def export(junit, output):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root/'tests/test_executive_adversarial.py').read_text(encoding='utf-8'))
    cases = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='CASES' for t in node.targets))
    records = {}
    for case in ET.parse(junit).iter('testcase'):
        props = {p.attrib['name']:p.attrib['value'] for p in case.findall('./properties/property')}
        if 'case_id' not in props:
            continue
        records.setdefault(props['case_id'], []).append({
            'backend': 'firestore-fake' if '[firestore' in case.attrib['name'] else
                       'sqlite' if '[sqlite' in case.attrib['name'] else 'provider/policy-only',
            'result': 'Fail' if case.find('failure') is not None or case.find('error') is not None else 'Pass',
            'actual_reply_or_tool_result': json.loads(props['actual']),
            'tool_calls': json.loads(props['tool_calls']),
            'test':case.attrib['name'],
        })
    result=[]
    for domain, values in cases.items():
        for index,(mode,text) in enumerate(values):
            identifier=f'{domain.upper()}-{index+1:02d}'
            runs=records.get(identifier,[])
            passed=bool(runs) and all(run['result']=='Pass' for run in runs)
            excluded=domain=='registration'
            result.append({'id':identifier,'domain':domain,'mode':mode,'user_input':text,
                'preconditions':'Disposable SQLite or strict Firestore fake; fixed clock; synthetic validated interpretation; isolated Calendar/Telegram mocks',
                'expected_behavior':EXPECTED[domain][index], 'executions':runs,
                'difference':'No assertion difference' if passed else 'Missing evidence or failed assertion; inspect test output',
                'result':'Pass' if passed else 'Blocked' if not runs else 'Fail',
                'severity':'None' if passed else 'High',
                'root_cause':None if passed else 'See executable assertion and associated JUnit failure',
                'repair':None if passed else 'Required before release',
                'acceptance_scope':'Excluded by owner; retained only as refusal safety coverage' if excluded else 'Active',
            })
    active=[r for r in result if r['domain']!='registration']
    document={'baseline':'07866f6','scope':'Seven active domains; registration removed by owner',
        'limitations':['Service-contract tests use synthetic interpretation and do not establish live language understanding.',
                       'Firestore is a strict fake; Calendar and Telegram transports are mocked.',
                       'Registration tests prove unsupported-operation refusal only; no registration feature exists.',
                       'Production status is documented separately; this artifact is local evidence.'],
        'unique_cases':len(result),'active_cases':len(active),
        'pass':sum(r['result']=='Pass' for r in active),'fail':sum(r['result']=='Fail' for r in active),
        'blocked':sum(r['result']=='Blocked' for r in active), 'cases':result}
    Path(output).write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in document.items() if k not in {'cases','limitations'}},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--junit',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    export(args.junit,args.output)
