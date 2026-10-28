"""Preserve image transport and exclude empty timeout serialization artifacts."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from alphadiana.analysis.actions.llm_annotations import ActionAnnotations
from alphadiana.results.trace import normalize_persisted_trajectory
from tests.analysis.test_general_analysis import judge_options, record, response, source

IMAGE='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='


def test_image_part_is_transmitted_and_changes_cache_identity(tmp_path):
    image={'type':'image_url','image_url':{'url':IMAGE,'detail':'high'}}
    raw=[{'role':'user','content':[{'type':'text','text':'Which shape?'},image]}]
    trace={'steps':[{'role':'user','content':'Which shape?'},{'role':'assistant','content':'A square.'}],
           'request':{'messages':[{'role':'user','content':'Which shape?\n'+json.dumps(image,sort_keys=True)}]}}
    artifact=source(tmp_path,[record('task',True,problem='Which shape?',token_usage={'prompt_tokens':1424},artifact_manifest={'files':{'normalized_trace':'trace.json','request_messages':'request.json'}})],
                    {'artifacts/trace.json':json.dumps(trace),'artifacts/request.json':json.dumps(raw)})
    calls=[]
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:
        def create(**kwargs):
            calls.append(kwargs)
            parts=kwargs['messages'][1]['content']
            assert isinstance(parts,list) and parts[1]==image
            assert 'data:image' not in parts[0]['text']
            evidence=json.loads(parts[0]['text'].split('\n',1)[1])
            assert evidence['capacity']['media_reference_prompt_tokens']==1424
            assert evidence['context']['media'][0]['source_id']=='request_messages/0/content/1'
            return response({'actions':[{'source_id':'steps/1/content','start':0,'end':9,'label':'Finalization'}]})
        plugin.client.chat.completions.create=create
        first=plugin.measure([artifact]); assert plugin.measure([artifact]).tables==first.tables
        assert len(calls)==1
        image['image_url']['detail']='low';raw[0]['content'][1]=image
        artifact.file(artifact.records()[0],'request_messages').write_text(json.dumps(raw))
        second=plugin.measure([artifact])
        assert len(calls)==2
        assert first.tables['annotations'][0]['annotation_sha256']!=second.tables['annotations'][0]['annotation_sha256']


def timeout_fixture():
    raw={'choices':[{'finish_reason':'timeout','message':{'role':'assistant','content':''}}]}
    trace={'steps':[{'role':'assistant','content':json.dumps({'content':'','role':'assistant'},sort_keys=True)}],
           'final_output':{'answer':None,'raw_output':'','finish_reason':'timeout'}}
    return {'metadata':{'directllm_timeout_scored_zero':True}},trace,raw


def test_empty_timeout_uses_raw_evidence_and_never_calls_judge(tmp_path):
    meta,trace,raw=timeout_fixture()
    artifact=source(tmp_path,[record('task',False,**meta,artifact_manifest={'files':{'normalized_trace':'trace.json','response_json':'response.json'}})],
                    {'artifacts/trace.json':json.dumps(trace),'artifacts/response.json':json.dumps(raw)})
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:
        def refuse(**kwargs):raise AssertionError('An empty timeout has no model action')
        plugin.client.chat.completions.create=refuse
        measured=plugin.measure([artifact])
        assert not measured.tables['annotations']
        assert measured.coverage['run']['traces_without_assistant_actions'][0]['task_id']=='task'
    assert normalize_persisted_trajectory([{'role':'assistant','content':''}])==[]


@pytest.mark.parametrize('partial', [True,False])
def test_partial_timeout_and_real_wrapper_text_are_preserved(partial):
    from alphadiana.results.trace import is_empty_timeout_trace
    record,trace,raw=timeout_fixture()
    if partial:trace['steps'][0]['content']='Partial reasoning before timeout.'
    else:raw['choices'][0]['message']['content']=trace['steps'][0]['content']
    assert not is_empty_timeout_trace(record,trace,raw)
    assert normalize_persisted_trajectory([{'role':'assistant','content':trace['steps'][0]['content']}])
