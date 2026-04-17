"""
Unit tests for the orchestrator system.
Tests sweep creation, job claiming, worker registration, and result submission.
"""

import os
import pytest
import tempfile
from datetime import datetime, timezone

from bot.orchestrator import OrchestratorDB, generate_grid_configs, split_into_batches


class TestConfigGeneration:
    """Test config generation from parameter ranges."""
    
    def test_generate_simple_grid(self):
        """Test basic grid generation."""
        params = {
            'h_min': 60,
            'h_max': 80,
            'h_step': 10,
            'x_min': 50,
            'x_max': 60,
            'x_step': 10,
            'smoothing': 'on'
        }
        
        configs = generate_grid_configs(params)
        
        assert len(configs) == 6  # 3 h values * 2 x values
        assert all('lookback_window' in c for c in configs)
        assert all('regression_level' in c for c in configs)
        assert all(c['use_kernel_smoothing'] is True for c in configs)
    
    def test_generate_grid_with_smoothing_both(self):
        """Test grid generation with both smoothing variants."""
        params = {
            'h_min': 60,
            'h_max': 60,
            'h_step': 10,
            'x_min': 50,
            'x_max': 50,
            'x_step': 10,
            'smoothing': 'both'
        }
        
        configs = generate_grid_configs(params)
        
        assert len(configs) == 2  # 1 h * 1 x * 2 smoothing
        assert any(c['use_kernel_smoothing'] is True for c in configs)
        assert any(c['use_kernel_smoothing'] is False for c in configs)
    
    def test_generate_grid_with_extra_params(self):
        """Test grid generation with additional parameters."""
        params = {
            'h_min': 60,
            'h_max': 60,
            'h_step': 10,
            'x_min': 50,
            'x_max': 50,
            'x_step': 10,
            'smoothing': 'on',
            'r_values': [5, 10, 15]
        }
        
        configs = generate_grid_configs(params)
        
        assert len(configs) == 3  # 1 h * 1 x * 3 r values
        assert configs[0]['relative_weight'] == 5
        assert configs[1]['relative_weight'] == 10
        assert configs[2]['relative_weight'] == 15
    
    def test_skip_invalid_vol_ranges(self):
        """Test that invalid vol_min >= vol_max configs are skipped."""
        params = {
            'h_min': 60,
            'h_max': 60,
            'h_step': 10,
            'x_min': 50,
            'x_max': 50,
            'x_step': 10,
            'smoothing': 'on',
            'vol_min_values': [5, 10],
            'vol_max_values': [8, 5]
        }
        
        configs = generate_grid_configs(params)
        
        # Should skip configs where vol_min >= vol_max
        assert all(c['volatility_min'] < c['volatility_max'] for c in configs)


class TestBatchSplitting:
    """Test splitting configs into job batches."""
    
    def test_split_exact_batches(self):
        """Test splitting when total is exact multiple of batch size."""
        configs = [{'id': i} for i in range(10)]
        batches = split_into_batches(configs, 5)
        
        assert len(batches) == 2
        assert len(batches[0]) == 5
        assert len(batches[1]) == 5
    
    def test_split_with_remainder(self):
        """Test splitting with remainder."""
        configs = [{'id': i} for i in range(11)]
        batches = split_into_batches(configs, 5)
        
        assert len(batches) == 3
        assert len(batches[0]) == 5
        assert len(batches[1]) == 5
        assert len(batches[2]) == 1
    
    def test_split_smaller_than_batch(self):
        """Test splitting when total is smaller than batch size."""
        configs = [{'id': i} for i in range(3)]
        batches = split_into_batches(configs, 10)
        
        assert len(batches) == 1
        assert len(batches[0]) == 3


class TestOrchestratorDB:
    """Test OrchestratorDB operations."""
    
    @pytest.fixture
    def db(self):
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        db = OrchestratorDB(path)
        yield db
        
        # Cleanup
        if os.path.exists(path):
            os.remove(path)
    
    def test_create_sweep(self, db):
        """Test sweep creation."""
        params = {
            'time_budget_minutes': 60,
            'param_space': {
                'h': [30, 110],
                'x': [50, 70],
                'smoothing': [True, False]
            }
        }
        
        sweep = db.create_sweep('Test Sweep', params, num_workers=2)
        
        assert sweep['id'] is not None
        assert sweep['name'] == 'Test Sweep'
        assert sweep['status'] == 'pending'
        assert sweep['total_configs'] == 2  # 2 workers
        assert sweep['completed_configs'] == 0
        assert sweep['batch_size'] == 1
    
    def test_list_sweeps(self, db):
        """Test listing sweeps."""
        params = {
            'time_budget_minutes': 60,
            'param_space': {'h': [30, 110], 'x': [50, 70], 'smoothing': [True, False]}
        }
        db.create_sweep('Sweep 1', params, num_workers=1)
        db.create_sweep('Sweep 2', params, num_workers=1)
        
        sweeps = db.list_sweeps()
        
        assert len(sweeps) == 2
        assert sweeps[0]['name'] == 'Sweep 2'  # Most recent first
        assert sweeps[1]['name'] == 'Sweep 1'
    
    def test_update_sweep_status(self, db):
        """Test updating sweep status."""
        params = {
            'time_budget_minutes': 60,
            'param_space': {'h': [30, 110], 'x': [50, 70], 'smoothing': [True, False]}
        }
        sweep = db.create_sweep('Test', params, num_workers=1)
        
        db.update_sweep_status(sweep['id'], 'running')
        
        updated = db.get_sweep(sweep['id'])
        assert updated['status'] == 'running'
    
    def test_delete_sweep(self, db):
        """Test sweep deletion."""
        params = {
            'time_budget_minutes': 60,
            'param_space': {'h': [30, 110], 'x': [50, 70], 'smoothing': [True, False]}
        }
        sweep = db.create_sweep('Test', params, num_workers=1)
        
        db.delete_sweep(sweep['id'])
        
        deleted = db.get_sweep(sweep['id'])
        assert deleted is None
    
    def test_register_worker(self, db):
        """Test worker registration."""
        worker_id = db.register_worker('Test Worker', 'test-host', {'cpu_count': 8})
        
        assert worker_id is not None
        
        worker = db.get_worker(worker_id)
        assert worker['name'] == 'Test Worker'
        assert worker['hostname'] == 'test-host'
        assert worker['status'] == 'idle'
    
    def test_register_worker_twice(self, db):
        """Test registering same worker twice updates it."""
        worker_id1 = db.register_worker('Test Worker', 'test-host', {'cpu_count': 8})
        worker_id2 = db.register_worker('Test Worker', 'test-host', {'cpu_count': 16})
        
        assert worker_id1 == worker_id2
        
        worker = db.get_worker(worker_id1)
        assert worker['machine_info']['cpu_count'] == 16
    
    def test_claim_job(self, db):
        """Test job claiming."""
        # Create sweep with jobs (smart mode creates 1 job per worker)
        params = {
            'time_budget_minutes': 60,
            'param_space': {'h': [30, 110], 'x': [50, 70], 'smoothing': [True, False]}
        }
        sweep = db.create_sweep('Test', params, num_workers=2)
        db.update_sweep_status(sweep['id'], 'running')
        
        # Register worker
        worker_id = db.register_worker('Test Worker', 'test-host', {})
        
        # Claim job
        job = db.claim_job(worker_id)
        
        assert job is not None
        assert job['id'] is not None
        assert job['sweep_id'] == sweep['id']
        assert len(job['configs']) == 1  # Smart mode: 1 config per job
        assert job['configs'][0]['mode'] == 'smart'
        
        # Verify worker status changed to busy
        worker = db.get_worker(worker_id)
        assert worker['status'] == 'busy'
        assert worker['current_job_id'] == job['id']
    
    def test_claim_job_no_pending(self, db):
        """Test claiming when no jobs are pending."""
        worker_id = db.register_worker('Test Worker', 'test-host', {})
        
        job = db.claim_job(worker_id)
        
        assert job is None
    
    def test_claim_job_paused_sweep(self, db):
        """Test that jobs from paused sweeps are not claimed."""
        # Create sweep but leave it paused
        params = {
            'time_budget_minutes': 60,
            'param_space': {'h': [30, 110], 'x': [50, 70], 'smoothing': [True, False]}
        }
        sweep = db.create_sweep('Test', params, num_workers=1)
        db.update_sweep_status(sweep['id'], 'paused')
        
        worker_id = db.register_worker('Test Worker', 'test-host', {})
        
        job = db.claim_job(worker_id)
        
        assert job is None
    
    def test_submit_job(self, db):
        """Test job submission."""
        # Create and claim job (smart mode: 1 worker = 1 job)
        params = {
            'time_budget_minutes': 60,
            'param_space': {'h': [30, 110], 'x': [50, 70], 'smoothing': [True, False]}
        }
        sweep = db.create_sweep('Test', params, num_workers=1)
        db.update_sweep_status(sweep['id'], 'running')
        worker_id = db.register_worker('Test Worker', 'test-host', {})
        job = db.claim_job(worker_id)
        
        # Submit results
        results = [
            {'config_hash': 'abc123', 'score': 1.5, 'eth_pf': 1.2}
        ]
        db.submit_job(job['id'], results)
        
        # Verify sweep progress
        updated_sweep = db.get_sweep(sweep['id'])
        assert updated_sweep['completed_configs'] == 1
        assert updated_sweep['status'] == 'completed'  # All jobs done (1 worker = 1 job)
        
        # Verify worker status
        worker = db.get_worker(worker_id)
        assert worker['status'] == 'idle'
        assert worker['current_job_id'] is None
    
    def test_heartbeat(self, db):
        """Test worker heartbeat."""
        worker_id = db.register_worker('Test Worker', 'test-host', {})
        
        worker1 = db.get_worker(worker_id)
        last_seen1 = worker1['last_seen']
        
        # Wait a moment and send heartbeat
        import time
        time.sleep(0.1)
        db.heartbeat(worker_id)
        
        worker2 = db.get_worker(worker_id)
        last_seen2 = worker2['last_seen']
        
        assert last_seen2 > last_seen1
    
    def test_list_workers(self, db):
        """Test listing workers."""
        db.register_worker('Worker 1', 'host1', {})
        db.register_worker('Worker 2', 'host2', {})
        
        workers = db.list_workers()
        
        assert len(workers) == 2
        assert any(w['name'] == 'Worker 1' for w in workers)
        assert any(w['name'] == 'Worker 2' for w in workers)


class TestJobLifecycle:
    """Test complete job lifecycle."""
    
    @pytest.fixture
    def db(self):
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        
        db = OrchestratorDB(path)
        yield db
        
        if os.path.exists(path):
            os.remove(path)
    
    def test_complete_lifecycle(self, db):
        """Test complete sweep -> claim -> submit lifecycle."""
        # 1. Create sweep (smart mode: 3 workers = 3 jobs)
        params = {
            'time_budget_minutes': 60,
            'param_space': {
                'h': [30, 110],
                'x': [50, 70],
                'smoothing': [True, False]
            }
        }
        sweep = db.create_sweep('Full Test', params, num_workers=3)
        db.update_sweep_status(sweep['id'], 'running')
        
        assert sweep['total_configs'] == 3  # 3 workers
        
        # 2. Register worker
        worker_id = db.register_worker('Test Worker', 'localhost', {'cpu_count': 8})
        
        # 3. Claim first job
        job1 = db.claim_job(worker_id)
        assert job1 is not None
        assert len(job1['configs']) == 1  # Smart mode: 1 config per job
        assert job1['configs'][0]['mode'] == 'smart'
        
        worker = db.get_worker(worker_id)
        assert worker['status'] == 'busy'
        
        # 4. Submit first job with 2 results
        results1 = [
            {'score': 1.5, 'config_hash': 'hash1'},
            {'score': 1.8, 'config_hash': 'hash2'}
        ]
        db.submit_job(job1['id'], results1)
        
        sweep_after_job1 = db.get_sweep(sweep['id'])
        assert sweep_after_job1['completed_configs'] == 2  # 2 results submitted
        assert sweep_after_job1['status'] == 'running'  # Not complete yet (2 < 3)
        
        # 5. Claim second job
        job2 = db.claim_job(worker_id)
        assert job2 is not None
        assert len(job2['configs']) == 1  # Smart mode: 1 config per job
        
        # 6. Submit second job with 1 result
        results2 = [
            {'score': 2.0, 'config_hash': 'hash3'}
        ]
        db.submit_job(job2['id'], results2)
        
        # 7. Verify sweep is complete
        final_sweep = db.get_sweep(sweep['id'])
        assert final_sweep['completed_configs'] == 3  # 3 results total
        assert final_sweep['status'] == 'completed'
        
        # 8. Verify no more jobs available
        job3 = db.claim_job(worker_id)
        assert job3 is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
