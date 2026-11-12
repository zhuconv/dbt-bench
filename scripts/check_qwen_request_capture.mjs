#!/usr/bin/env node
// Exercise the locked native request generator/logger against a local fixture
// server. No benchmark, model service, production proxy or agent loop is used.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const [packageRoot, output] = process.argv.slice(2).map(p=>resolve(p));
if (!packageRoot || !output) throw new Error('Usage: node check_qwen_request_capture.mjs PACKAGE_ROOT NEW_OUTPUT_DIR');
await mkdir(output, {recursive:false});
const chunks = join(packageRoot, 'chunks');
const {createOpenAIContentGenerator} = await import(pathToFileURL(join(chunks,'chunk-AVVRKAT2.js')));
const {LoggingContentGenerator} = await import(pathToFileURL(join(chunks,'loggingContentGenerator-AS4MQBEB.js')));
const {preloadRuntimeFetchModule} = await import(pathToFileURL(join(chunks,'chunk-ZZZAYAJW.js')));
await preloadRuntimeFetchModule();
const metadata = JSON.parse(await readFile(join(packageRoot,'package.json')));
assert.equal(metadata.version,'0.26.5');
const wire = [], rows = [];
let activeCase = '', caseRequests = 0;
const server = createServer(async(req,res)=>{
  const parts=[];
  for await(const part of req) parts.push(part);
  const body=JSON.parse(Buffer.concat(parts).toString());
  const id=`fixture-${activeCase}-${++caseRequests}`;
  wire.push({case:activeCase,id,url:req.url,body});
  // Demonstrate SDK retries: the native logger records a completed interaction,
  // not necessarily every HTTP attempt. This limitation must stay explicit.
  if(activeCase==='retry' && caseRequests===1){
    res.writeHead(503,{'content-type':'application/json'});
    res.end(JSON.stringify({error:{message:'fixture temporary failure',type:'server_error'}}));return;
  }
  if(activeCase==='http_error'){
    res.writeHead(400,{'content-type':'application/json'});
    res.end(JSON.stringify({error:{message:'fixture deliberate error',type:'invalid_request_error'}}));return;
  }
  const message={role:'assistant',content:'fixture answer',reasoning_content:'fixture thought',
    tool_calls:[{id:'fixture-call-a',type:'function',function:{name:'inspect',arguments:'{"value":"A"}'}},
                {id:'fixture-call-b',type:'function',function:{name:'inspect',arguments:'{"value":"B"}'}}]};
  if(body.stream){
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const delta of [{role:'assistant',reasoning_content:'fixture thought'},
                        {content:'fixture answer'},
                        {tool_calls:message.tool_calls.map((call,index)=>({...call,index}))}]){
      res.write(`data: ${JSON.stringify({id,object:'chat.completion.chunk',created:1700000000,model:body.model,choices:[{index:0,delta,finish_reason:null}]})}\n\n`);
    }
    res.write(`data: ${JSON.stringify({id,object:'chat.completion.chunk',created:1700000000,model:body.model,choices:[{index:0,delta:{},finish_reason:'tool_calls'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}})}\n\n`);
    res.end('data: [DONE]\n\n');
  }else{
    res.writeHead(200,{'content-type':'application/json'});
    res.end(JSON.stringify({id,object:'chat.completion',created:1700000000,model:body.model,
      choices:[{index:0,message,finish_reason:'tool_calls'}],usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120}}));
  }
});
await new Promise((yes,no)=>{server.once('error',no);server.listen(0,'127.0.0.1',yes);});
const baseUrl=`http://127.0.0.1:${server.address().port}/v1`;
const sessionId='a0000000-0000-4000-8000-000000000000';
const configuration={
  getWorkingDir:()=>output, getCliVersion:()=>metadata.version, getProxy:()=>undefined,
  getSessionId:()=>sessionId, getTelemetryUserId:()=>undefined,
  getTelemetryEnabled:()=>false, getTelemetryLogPromptsEnabled:()=>false,
  getTelemetrySensitiveSpanAttributeMaxLength:()=>1000000,
  getModelRegistry:()=>undefined, getContentGeneratorConfig:()=>undefined,
  getExperimental:()=>({}), getDebugMode:()=>false,
};
const missingMethods=new Set();
const config=new Proxy(configuration,{get(target,key){
  if(key in target)return target[key];
  // Fixture-only CLI configuration. These defaults never touch production settings.
  if(typeof key==='string'&&key.startsWith('get')){missingMethods.add(key);return()=>undefined;}
  return undefined;
}});
try{
  for(const kind of ['stream','nonstream','retry','http_error']){
    activeCase=kind;caseRequests=0;
    const logDir=join(output,kind);await mkdir(logDir);
    const generatorConfig={model:'qwen3.6-35b-a3b',authType:'openai',apiKey:'local-fixture-not-a-secret',
      baseUrl,maxRetries:kind==='retry'?1:0,enableOpenAILogging:true,openAILoggingDir:logDir,
      timeout:10000,contextWindowSize:131072,
      extra_body:{chat_template_kwargs:{enable_thinking:true}}};
    const native=createOpenAIContentGenerator(generatorConfig,config);
    const logger=new LoggingContentGenerator(native,config,generatorConfig);
    const request={model:generatorConfig.model,contents:[
      {role:'user',parts:[{text:'fixture user with memory snapshot'}]},
      {role:'model',parts:[{functionCall:{id:'previous-a',name:'inspect',args:{value:'large'}}},
                           {functionCall:{id:'previous-b',name:'inspect',args:{value:'empty'}}}]},
      {role:'user',parts:[{functionResponse:{id:'previous-a',name:'inspect',response:{error:'fixture tool error '+ 'z'.repeat(120000)}}},
                          {functionResponse:{id:'previous-b',name:'inspect',response:{output:''}}}]}],
      config:{systemInstruction:'fixture exact system prompt',temperature:0.6,topP:0.95,maxOutputTokens:32768,
        tools:[{functionDeclarations:[{name:'inspect',description:'fixture tool',parameters:{type:'object',properties:{value:{type:'string'}},required:['value']}}]}]}};
    let failure=null;
    const outputs=[];
    try{
      if(kind==='nonstream')outputs.push(await logger.generateContent(request,sessionId+'########0'));
      else for await(const response of await logger.generateContentStream(request,sessionId+'########0'))outputs.push(response);
    }catch(error){failure={name:error.name,message:error.message};}
    const files=(await readdir(logDir)).filter(f=>f.endsWith('.json'));
    assert.equal(files.length,1,kind+' interaction count');
    const captured=JSON.parse(await readFile(join(logDir,files[0])));
    const sent=wire.filter(x=>x.case===kind);
    assert.deepEqual(captured.request,sent.at(-1).body,kind+' recorded body differs from HTTP body');
    assert.equal(captured.context.sessionId,sessionId);
    assert.equal(typeof captured.request.messages[0].content,'string');
    assert.equal(captured.request.messages[0].content,'fixture exact system prompt');
    assert.equal(captured.request.tools.length,1);
    assert.ok(JSON.stringify(captured.request).includes('z'.repeat(120000)),'long tool payload omitted');
    if(kind==='http_error'){
      assert.ok(failure&&captured.error&&captured.response===null);
    }else{
      assert.equal(failure,null);
      assert.equal(captured.response.choices[0].message.content,'fixture answer');
      assert.equal(captured.response.choices[0].message.reasoning_content,'fixture thought');
      assert.deepEqual(captured.response.choices[0].message.tool_calls.map(c=>c.id),['fixture-call-a','fixture-call-b']);
    }
    assert.equal(sent.length,kind==='retry'?2:1);
    rows.push({case:kind,passed:true,wire_attempts:sent.length,interaction_logs:files.length,
      request_body_matches_wire:true,all_http_attempts_recorded:sent.length===files.length,
      raw_log:join(logDir,files[0]),failure});
  }
}catch(error){
  rows.push({case:activeCase,passed:false,error:{name:error.name,message:error.message,stack:error.stack}});
  process.exitCode=1;
}finally{
  server.closeAllConnections();await new Promise(yes=>server.close(yes));
  await writeFile(join(output,'wire.json'),JSON.stringify(wire,null,2)+'\n');
  const sources=[];
  for(const name of ['chunk-AVVRKAT2.js','chunk-YAJW4A3X.js','chunk-WKKB6FVL.js','loggingContentGenerator-AS4MQBEB.js']){
    sources.push({path:join(chunks,name),sha256:createHash('sha256').update(await readFile(join(chunks,name))).digest('hex')});
  }
  const result={schema_version:1,kind:'native_qwen_request_capture_conformance',qwen_version:metadata.version,
    node_version:process.version,benchmark_trials:0,model_requests:0,fixture_http_requests:wire.length,
    passed:rows.length===4&&rows.every(r=>r.passed),cases:rows,sources,
    fixture_configuration_defaults:[...missingMethods].sort(),
    scope:'Native generator, SDK HTTP transport and native logger. Recorded request body equals the received JSON body in these fixtures. Reconstructed response is not a raw HTTP response; SDK retries are not individually logged.'};
  await writeFile(join(output,'summary.json'),JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify({passed:result.passed,cases:rows.map(r=>({case:r.case,passed:r.passed,error:r.error?.message})),summary:join(output,'summary.json')}));
}
