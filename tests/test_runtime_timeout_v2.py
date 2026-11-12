"""Behavioral CPU regressions; run with the isolated timeout-v2 Python."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
import json
import os
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

try:
    import httpx
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.environments.singularity import server
    from harbor.models.trial.error_policy import policy_for
    from harbor.trial.errors import AgentTimeoutError, VerifierTimeoutError
    from harbor.trial.trial import Trial
except ImportError:
    SingularityEnvironment = server = None

IS_TIMEOUT_V2 = (hasattr(server, '_Execution') and
                 hasattr(SingularityEnvironment, '_confirm_exec_stopped'))
requires_timeout_v2 = unittest.skipUnless(IS_TIMEOUT_V2, 'Use the isolated timeout-v2 Python; do not patch the base runtime')

@asynccontextmanager
async def noop(*args, **kwargs):
    yield


def client_env(transport):
    env = object.__new__(SingularityEnvironment)
    env._server_port = 1
    env._http_client = httpx.AsyncClient(transport=transport)
    env._memory_limit_exceeded = None
    env._resolve_user = lambda user: None
    env._merge_env = lambda value: value or {}
    return env


def fake_trial(run):
    return SimpleNamespace(agent=SimpleNamespace(run=run, extra_env={}), user_agent=None,
        agent_environment=SimpleNamespace(with_default_user=lambda _:nullcontext(),
                                          scoped_exec_env=lambda _:nullcontext()),
        _emit=AsyncMock(), _now=lambda:None, _configure_web_tools=AsyncMock(),
        _network_plan=lambda *a,**k:SimpleNamespace(agent_env_baseline=None,agent_phase=None),
        _phase_network_policy=noop, _log_context=lambda *a,**k:nullcontext(),
        paths=SimpleNamespace(agent_dir=Path('/tmp')))


@requires_timeout_v2
class PhaseTests(unittest.IsolatedAsyncioTestCase):
    async def agent(self, action, budget):
        with patch('harbor.trial.sync_trajectory.sync_trajectory', noop):
            return await Trial._run_agent_phase(fake_trial(action), target=SimpleNamespace(),
                                               instruction='CPU fixture', timeout_sec=budget,user=None)

    async def test_agent_real_budget_is_valid_failure(self):
        async def run(**kw): await asyncio.sleep(10)
        with self.assertRaises(AgentTimeoutError): await self.agent(run, 0.03)
        self.assertTrue(policy_for('AgentTimeoutError').reward_valid)

    async def test_agent_inner_timeout_is_not_budget(self):
        async def run(**kw): raise TimeoutError('inner transport fixture')
        with self.assertRaisesRegex(TimeoutError, 'inner transport') as cm: await self.agent(run, 5)
        self.assertNotIsInstance(cm.exception, AgentTimeoutError)

    async def test_verifier_real_budget(self):
        with self.assertRaises(VerifierTimeoutError):
            await Trial._verify_with_timeout(None,SimpleNamespace(verify=lambda:asyncio.sleep(10)),0.03)

    async def test_verifier_inner_timeout_preserved(self):
        async def run(): raise TimeoutError('inner verifier transport')
        with self.assertRaisesRegex(TimeoutError, 'inner verifier') as cm:
            await Trial._verify_with_timeout(None,SimpleNamespace(verify=run),5)
        self.assertNotIsInstance(cm.exception,VerifierTimeoutError)

    async def test_agent_and_verifier_complete_within_resolved_budget(self):
        async def run(**kw): await asyncio.sleep(.02)
        await self.agent(run, .2)
        result=await Trial._verify_with_timeout(None,SimpleNamespace(verify=lambda:asyncio.sleep(.02,result=37)),.2)
        self.assertEqual(result,37)


@requires_timeout_v2
class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_unbounded_read_finite_connect_and_explicit_command_deadline(self):
        requests=[]
        async def handler(req):
            requests.append(req)
            return httpx.Response(200,json={'stdout':'done','stderr':None,'return_code':0})
        env=client_env(httpx.MockTransport(handler))
        try:
            await env.exec('fixture')
            await env.exec('fixture',timeout_sec=1234)
        finally:await env._http_client.aclose()
        self.assertEqual([json.loads(r.content)['timeout_sec'] for r in requests],[None,1234])
        self.assertNotEqual(json.loads(requests[0].content)['exec_id'],json.loads(requests[1].content)['exec_id'])
        for r in requests:
            self.assertIsNone(r.extensions['timeout']['read'])
            self.assertTrue(all(r.extensions['timeout'][k]>0 for k in ('connect','write','pool')))

    async def test_transport_timeout_is_invalid_and_cancelled(self):
        requests=[]
        async def handler(req):
            requests.append(req)
            if req.url.path.endswith('/cancel'):
                return httpx.Response(200,json={'exec_id':req.url.path.split('/')[-2],'stopped':True})
            raise httpx.ReadTimeout('fixture',request=req)
        env=client_env(httpx.MockTransport(handler))
        try:
            with self.assertRaises(RuntimeError) as cm:await env.exec('fixture')
            self.assertEqual(type(cm.exception).__name__,'ExecTransportError')
            self.assertIsInstance(cm.exception.__cause__,httpx.ReadTimeout)
            self.assertFalse(policy_for(type(cm.exception).__name__).reward_valid)
            self.assertEqual(len(requests),2)
        finally:await env._http_client.aclose()

    async def test_unconfirmed_cleanup_prevents_later_verifier_execution(self):
        requests=[]
        async def handler(req):
            requests.append(req)
            raise httpx.ConnectError('fixture server lost',request=req)
        env=client_env(httpx.MockTransport(handler))
        try:
            with self.assertRaises(RuntimeError) as cm:await env.exec('fixture')
            self.assertEqual(type(cm.exception).__name__,'ExecCleanupError')
            with self.assertRaises(RuntimeError):await env.exec('verifier must not run')
            self.assertEqual(len(requests),2)
        finally:await env._http_client.aclose()

    async def test_repeated_cancellation_waits_for_stop_ack(self):
        started=asyncio.Event();stopping=asyncio.Event();ack=asyncio.Event()
        async def handler(req):
            if req.url.path.endswith('/cancel'):
                stopping.set();await ack.wait()
                return httpx.Response(200,json={'exec_id':req.url.path.split('/')[-2],'stopped':True})
            started.set();await asyncio.sleep(10)
        env=client_env(httpx.MockTransport(handler));task=asyncio.create_task(env.exec('fixture'))
        try:
            await started.wait();task.cancel();await stopping.wait();task.cancel();await asyncio.sleep(.03)
            self.assertFalse(task.done());ack.set()
            with self.assertRaises(asyncio.CancelledError):await task
        finally:await env._http_client.aclose()


@requires_timeout_v2
class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_deadline_preserves_partial_output(self):
        state=server._Execution();result=await state.run(server.CommandRequest(command='printf partial; sleep 20',timeout_sec=1,cwd='/tmp'))
        self.assertEqual(result.return_code,124);self.assertEqual(result.stdout,'partial');self.assertFalse(state.remember())

    async def test_cancel_stops_term_ignoring_detached_child_and_preserves_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'submission';pidfile=Path(tmp)/'child'
            child="import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(%r).write_text(str(os.getpid()));p=Path(%r);\nwhile True:\n p.write_text(str(time.monotonic()));time.sleep(.02)"%(str(pidfile),str(out))
            parent="import subprocess,sys,time,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,'-c',%r],start_new_session=True);time.sleep(60)"%child
            state=server._Execution();task=asyncio.create_task(state.run(server.CommandRequest(command=shlex.quote(sys.executable)+' -c '+shlex.quote(parent),cwd=tmp)))
            peer=server._Execution();peer_task=asyncio.create_task(peer.run(server.CommandRequest(command='sleep 2; printf peer-alive',cwd=tmp)))
            try:
                for _ in range(100):
                    if out.exists():break
                    await asyncio.sleep(.03)
                self.assertTrue(out.exists());await state.stop();await asyncio.wait_for(task,5)
                value=out.read_bytes();await asyncio.sleep(.15)
                self.assertEqual(out.read_bytes(),value);self.assertFalse(state.remember())
                self.assertEqual((await peer_task).stdout,'peer-alive')
            finally:
                await state.stop();await peer.stop()
                await asyncio.gather(task,peer_task,return_exceptions=True)

    async def test_rapid_reparented_child_between_ancestry_samples_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile=Path(tmp)/'orphan.pid'
            leaf="import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid()));time.sleep(30)"%str(pidfile)
            middle="import subprocess,sys;subprocess.Popen([sys.executable,'-c',%r],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"%leaf
            parent="import subprocess,sys,time;subprocess.run([sys.executable,'-c',%r]);time.sleep(30)"%middle
            state=server._Execution()
            # Force the real intermediary to exit between ancestry samples.
            state.monitor=lambda:asyncio.sleep(30)
            task=asyncio.create_task(state.run(server.CommandRequest(command=shlex.quote(sys.executable)+' -c '+shlex.quote(parent),cwd=tmp)))
            try:
                for _ in range(100):
                    if pidfile.exists():break
                    await asyncio.sleep(.02)
                self.assertTrue(pidfile.exists());await asyncio.sleep(.1)
                pid=int(pidfile.read_text());self.assertNotIn(pid,state.known)
                await state.stop();await asyncio.wait_for(task,5)
                stat=Path('/proc')/str(pid)/'stat'
                self.assertTrue(not stat.exists() or stat.read_text().rsplit(')',1)[1].split()[0]=='Z')
            finally:
                if pidfile.exists():
                    try:os.kill(int(pidfile.read_text()),9)
                    except ProcessLookupError:pass
                await state.stop();await task

    async def test_cancel_before_launch_tombstone_and_idempotent_ack(self):
        ident='before-launch-fixture'
        try:
            first=await server.cancel_command(ident);second=await server.cancel_command(ident)
            result=await server.exec_command(server.CommandRequest(exec_id=ident,command='exit 99'),SimpleNamespace())
            self.assertEqual(first,second);self.assertTrue(first['stopped']);self.assertEqual(result.return_code,143)
        finally:server._EXECUTIONS.pop(ident,None)


if __name__=='__main__':unittest.main()
