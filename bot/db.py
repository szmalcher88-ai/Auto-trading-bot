"""
Database operations for AutoResearch using Prisma.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from prisma import Prisma


class AutoResearchDB:
    """Handles database operations for autoresearch results."""

    def __init__(self):
        self.db = Prisma()
        self._connected = False
    
    async def connect(self):
        """Connect to database."""
        if not self._connected:
            await self.db.connect()
            self._connected = True
    
    async def disconnect(self):
        """Disconnect from database."""
        if self._connected:
            await self.db.disconnect()
            self._connected = False
    
    async def create_run(self, total_combinations: int, assets: List[str]) -> str:
        """Create a new autoresearch run record."""
        await self.connect()
        run = await self.db.autoresearchrun.create(
            data={
                'totalCombinations': total_combinations,
                'assetsCount': len(assets),
                'assets': ','.join(assets),
                'status': 'running'
            }
        )
        return run.id
    
    async def complete_run(self, run_id: str, duration_seconds: int):
        """Mark a run as completed."""
        await self.connect()
        await self.db.autoresearchrun.update(
            where={'id': run_id},
            data={
                'completedAt': datetime.now(timezone.utc),
                'durationSeconds': duration_seconds,
                'status': 'completed'
            }
        )
    
    async def save_result(self, run_id: str, config: Dict[str, Any], metrics: Dict[str, Dict[str, float]], score: float):
        """Save a single configuration result."""
        await self.connect()
        
        data = {
            'runId': run_id,
            'lookbackWindow': config['lookback_window'],
            'regressionLevel': config['regression_level'],
            'useKernelSmoothing': config['use_kernel_smoothing'],
            'relativeWeight': config['relative_weight'],
            'lag': config['lag'],
            'atrPeriod': config['atr_period'],
            'atrMultiplier': config['atr_multiplier'],
            'volatilityMin': config['volatility_min'],
            'volatilityMax': config['volatility_max'],
            'reentryDelay': config['re_entry_delay'],
            'score': score,
        }
        
        for symbol, m in metrics.items():
            prefix = symbol.replace('USDT', '').lower()
            data[f'{prefix}Trades'] = m.get('n_trades', 0)
            data[f'{prefix}ProfitFactor'] = m.get('profit_factor', 0.0)
            data[f'{prefix}MaxDrawdown'] = m.get('max_drawdown_pct', 0.0)
            data[f'{prefix}Profit'] = m.get('net_profit_pct', 0.0)
        
        await self.db.autoresearchresult.create(data=data)
    
    async def get_all_results(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get all results ordered by score."""
        await self.connect()
        results = await self.db.autoresearchresult.find_many(
            order={'score': 'desc'},
            take=limit,
            include={'run': True}
        )
        return [self._result_to_dict(r) for r in results]
    
    async def get_top_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get top N results by score."""
        await self.connect()
        results = await self.db.autoresearchresult.find_many(
            where={'score': {'gt': 0}},
            order={'score': 'desc'},
            take=limit,
            include={'run': True}
        )
        return [self._result_to_dict(r) for r in results]
    
    async def get_latest_run(self) -> Optional[Dict[str, Any]]:
        """Get the most recent completed run metadata."""
        await self.connect()
        run = await self.db.autoresearchrun.find_first(
            where={'status': 'completed'},
            order={'startedAt': 'desc'}
        )
        if not run:
            return None
        return self._run_to_dict(run)

    def _run_to_dict(self, run) -> Dict[str, Any]:
        return {
            'date': run.startedAt.isoformat(),
            'combinations_tested': run.totalCombinations,
            'duration_seconds': run.durationSeconds or 0,
            'assets': run.assets.split(',') if run.assets else [],
            'status': run.status,
        }
    
    async def get_results_for_latest_run(self) -> List[Dict[str, Any]]:
        """Get all results from the most recent completed run."""
        await self.connect()
        run = await self.db.autoresearchrun.find_first(
            where={'status': 'completed'},
            order={'startedAt': 'desc'}
        )
        if not run:
            return []

        results = await self.db.autoresearchresult.find_many(
            where={'runId': run.id},
            order={'score': 'desc'}
        )
        return [self._result_to_dict(r) for r in results]

    async def get_all_time_top_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get top N results across all runs, deduplicated by config fingerprint.

        Deduplication keeps the highest-scoring row for each unique parameter
        combination (lookbackWindow + regressionLevel + useKernelSmoothing +
        relativeWeight + lag + atrPeriod + atrMultiplier + volatilityMin +
        volatilityMax + reentryDelay).
        """
        await self.connect()
        # Fetch a large pool first, then deduplicate in Python (SQLite has no
        # DISTINCT ON, and the result set is always small enough for this).
        candidates = await self.db.autoresearchresult.find_many(
            where={'score': {'gt': 0}},
            order={'score': 'desc'},
            take=limit * 20,  # fetch extra to survive deduplication
            include={'run': True},
        )

        seen = {}
        for r in candidates:
            key = (
                r.lookbackWindow, r.regressionLevel, r.useKernelSmoothing,
                r.relativeWeight, r.lag, r.atrPeriod, r.atrMultiplier,
                r.volatilityMin, r.volatilityMax, r.reentryDelay,
            )
            if key not in seen:
                seen[key] = r

        deduped = sorted(seen.values(), key=lambda x: x.score, reverse=True)[:limit]
        return [self._result_to_dict(r) for r in deduped]

    async def get_results_by_params(
        self,
        lookback_window: int,
        regression_level: int,
        use_kernel_smoothing: bool,
    ) -> Optional[Dict[str, Any]]:
        """Find the best result for a specific parameter combination."""
        await self.connect()
        result = await self.db.autoresearchresult.find_first(
            where={
                'lookbackWindow': lookback_window,
                'regressionLevel': regression_level,
                'useKernelSmoothing': use_kernel_smoothing,
            },
            order={'score': 'desc'},
        )
        if not result:
            return None
        return self._result_to_dict(result)

    def _result_to_dict(self, result) -> Dict[str, Any]:
        """Convert Prisma result model to dictionary.

        Includes both long-form keys (``lookback_window``) and short aliases
        (``lookback``, ``regression``, ``smoothing``) so the response is
        compatible with both the CSV-format frontend table and the DB path.
        """
        d = {
            # Long-form (canonical)
            'lookback_window': result.lookbackWindow,
            'regression_level': result.regressionLevel,
            'use_kernel_smoothing': result.useKernelSmoothing,
            'relative_weight': result.relativeWeight,
            'lag': result.lag,
            'atr_period': result.atrPeriod,
            'atr_multiplier': result.atrMultiplier,
            'volatility_min': result.volatilityMin,
            'volatility_max': result.volatilityMax,
            're_entry_delay': result.reentryDelay,
            'score': result.score,
            # Short aliases expected by frontend table columns
            'lookback': result.lookbackWindow,
            'regression': result.regressionLevel,
            'smoothing': result.useKernelSmoothing,
            'vol_min': result.volatilityMin,
            'vol_max': result.volatilityMax,
            'reentry_delay': result.reentryDelay,
            # Placeholders for CSV-only fields not stored in DB
            'balanced_score': 0.0,
            'confidence': 0.0,
        }

        for prefix in ['eth', 'btc', 'sol', 'avax', 'bnb', 'ada', 'doge', 'matic']:
            trades = getattr(result, f'{prefix}Trades', None)
            if trades is not None:
                d[f'{prefix}_trades'] = trades
                d[f'{prefix}_pf'] = getattr(result, f'{prefix}ProfitFactor', 0.0)
                d[f'{prefix}_dd'] = getattr(result, f'{prefix}MaxDrawdown', 0.0)
                d[f'{prefix}_profit'] = getattr(result, f'{prefix}Profit', 0.0)

        return d


def run_async(coro):
    """Helper to run async functions synchronously."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
