"""Read-only external-stop evidence, separate from native task budget failures."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from dbt_bench import io


def timestamp(value, event=None):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        offset = (event or {}).get('local_utc_offset_sec')
        zone = timezone(timedelta(seconds=offset)) if offset is not None else ZoneInfo('America/Chicago')
        parsed = parsed.replace(tzinfo=zone)
    return parsed.timestamp()


def context(run, identity, root):
    """Bind a signal to this batch and member. No inferred timeout from prose."""
    run, root = Path(run).resolve(), Path(root)
    batch_name = identity.get('shared_model_batch')
    if not batch_name:
        return None
    batch = Path(batch_name).resolve(); manifest_path = batch / 'manifest.json'
    manifest = io.read_json(manifest_path)
    if manifest['experiment_id'] != io.digest(manifest['identity']):
        raise ValueError('Batch identity changed')
    entries = [e for e in manifest['identity']['members'] if Path(e['run']).resolve() == run]
    if len(entries) != 1 or not io.evidence_ok(entries[0]['manifest'], root):
        raise ValueError('Run does not belong to the recorded model batch')
    event_path = batch / 'interruption.json'
    if event_path.exists():
        event = io.read_json(event_path)
        if (event.get('kind') != 'external_process_signal' or event.get('signal_number') not in (2, 15)
                or Path(event['batch']).resolve() != batch or event['experiment_id'] != manifest['experiment_id']
                or type(event.get('observed_at')) not in (int, float)):
            raise ValueError('External signal evidence is invalid or belongs to another batch')
        launch = io.read_json(batch / 'launch.json')
        if str(launch['slurm_job_id']) != str(event['slurm_job_id']):
            raise ValueError('Signal belongs to another allocation')
        active = event.get('active_member')
        unstarted = {str(Path(p).resolve()) for p in event.get('unstarted_members', [])}
        affected = (active is not None and Path(active).resolve() == run) or str(run) in unstarted
        return {'kind': 'external_process_signal', 'event': event, 'affects_missing_slots': affected,
                'evidence': [io.evidence_ref(event_path, root), io.evidence_ref(manifest_path, root),
                             io.evidence_ref(batch / 'launch.json', root)],
                'scope': 'Observed external process signal, not inferred native task-budget exhaustion.'}
    # Older sealed runners record an exception after cleanup, without signal
    # time. Do not use cleanup's finished_at as the actual signal time.
    final = batch / 'launch-result.json'
    if final.exists():
        state = io.read_json(final)
        if (state.get('error') or {}).get('type') == 'BatchInterrupted':
            if state.get('experiment_id') != manifest['experiment_id']:
                raise ValueError('Legacy interruption belongs to another experiment')
            member_final = run / 'launch-result.json'
            member_interrupted = member_final.exists() and (io.read_json(member_final).get('error') or {}).get('type') == 'BatchInterrupted'
            affected = member_interrupted or str(run) in state.get('unstarted_members', [])
            return {'kind': 'legacy_external_stop_without_timestamp', 'event': None, 'affects_missing_slots': affected,
                    'evidence': [io.evidence_ref(final, root), io.evidence_ref(manifest_path, root)],
                    'scope': 'External stop observed; exact boundary for the last trial needs review.'}
    return None


def grading_finished(raw, cutoff, event):
    end = timestamp(raw.get('finished_at'), event)
    if end is not None and end < cutoff:
        return True
    rewards = (raw.get('verifier_result') or {}).get('rewards')
    if not rewards:
        return False
    # A signal during teardown must not erase a fully scored behavioral failure.
    phases = [timestamp((raw.get(k) or {}).get('finished_at'), event)
              for k in ('agent_execution', 'verifier')]
    return all(end is not None and end < cutoff for end in phases)


def classify(raw, stop, *, last_observed=False):
    if not stop or (stop['event'] is None and not stop.get('affects_missing_slots', False)):
        return {'status': 'no_external_stop_evidence', 'replacement_required': False, 'evidence': []}
    if stop['event'] is None:
        cancelled = (raw.get('exception_info') or {}).get('exception_type') == 'CancelledError'
        if not raw.get('finished_at') or cancelled:
            status = 'external_interrupted'
        else:
            status = 'pending_boundary_review' if last_observed else 'completed_before_external_stop'
    else:
        event = stop['event']; cutoff = timestamp(event['observed_at'])
        if grading_finished(raw, cutoff, event):
            status = 'completed_before_external_stop'
        elif timestamp(raw.get('started_at'), event) is None:
            status = 'pending_boundary_review'
        else:
            status = 'external_interrupted'
    return {'status': status, 'replacement_required': status == 'external_interrupted',
            'evidence': stop['evidence'], 'native_scores_unchanged': True}


def missing_slot_status(stop, *, has_unfinished_artifact):
    affected = stop and stop.get('affects_missing_slots', False)
    if affected and has_unfinished_artifact:
        return 'external_interrupted_missing_result'
    if affected:
        return 'not_started_due_to_external_stop'
    return 'incomplete_result_pending_review' if has_unfinished_artifact else 'not_started'


def scan_results(job):
    """Never discard malformed/missing terminal records or shift later slots."""
    job = Path(job); results, incomplete, starts = [], [], {}
    if not job.exists():
        return results, incomplete
    for folder in sorted(p for p in job.iterdir() if p.is_dir()):
        result = folder / 'result.json'
        known_artifacts = [p for p in (result, folder / 'config.json', folder / 'trial.log',
                                      folder / 'exception.txt', folder / 'lock.json') if p.is_file()]
        observed = known_artifacts or (folder/'agent').is_dir() or (folder/'verifier').is_dir()
        if not observed:
            continue
        try:
            raw = io.read_json(result)
            if not isinstance(raw, dict) or not all(raw.get(k) for k in ('id', 'started_at', 'config')):
                raise ValueError('Incomplete native identity')
            starts[result] = timestamp(raw['started_at'])
            timestamp(raw.get('finished_at'))
            for phase in ('agent_execution', 'verifier'):
                if raw.get(phase) is not None:
                    if not isinstance(raw[phase], dict):raise ValueError('Invalid native timing record')
                    timestamp(raw[phase].get('finished_at'))
            results.append((result, raw))
        except (OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
            incomplete.append({'trial_dir': str(folder), 'reason': type(exc).__name__ + ': ' + str(exc),
                               'files': known_artifacts})
    results.sort(key=lambda p: (starts[p[0]], p[1]['id']))
    return results, incomplete
