import { describe, expect, it } from 'vitest';

import { backoffMs, check, GatewayRefusal, lateProducts, rowsToItems } from '../nodes/PavedRoad/logic';

describe('rows to items', () => {
	it('keys each row by column', () => {
		expect(rowsToItems({ status: 'ok', columns: ['channel', 'trades'], rows: [['app', 3], ['web', 1]] })).toEqual([
			{ channel: 'app', trades: 3 },
			{ channel: 'web', trades: 1 },
		]);
	});
	it('handles an empty result', () => {
		expect(rowsToItems({ status: 'ok', columns: ['x'], rows: [] })).toEqual([]);
	});
});

describe('refusals', () => {
	it('passes answers through', () => {
		expect(check({ status: 'ok', rows: [] }, 200).status).toBe('ok');
	});
	it('turns a policy refusal into a non-retryable error with the message', () => {
		try {
			check({ status: 'refused', rule: 'denied_column', message: 'n8n (automation) may not read amount_eur' }, 403);
			expect.unreachable();
		} catch (e) {
			expect(e).toBeInstanceOf(GatewayRefusal);
			expect((e as GatewayRefusal).rule).toBe('denied_column');
			expect((e as GatewayRefusal).retryable).toBe(false);
			expect((e as Error).message).toContain('amount_eur');
		}
	});
	it('marks a full queue as retryable', () => {
		expect(() => check({ status: 'refused', rule: 'queue_full', message: 'busy' }, 429)).toThrow(GatewayRefusal);
		try {
			check({ status: 'refused', rule: 'queue_full' }, 429);
		} catch (e) {
			expect((e as GatewayRefusal).retryable).toBe(true);
		}
	});
});

describe('freshness', () => {
	const p = (name: string, through: string | null) => ({
		name, table: `raw.${name}`, data_through: through, freshness: { expected_by: '06:00' }, owner: { team: 't', contact: '#t' },
	});
	it('lists products without yesterday', () => {
		const late = lateProducts([p('trades', '2026-09-28'), p('cash', '2026-09-27'), p('new', null)], '2026-09-29');
		expect(late.map((x) => x.name)).toEqual(['cash', 'new']);
	});
});

describe('backoff', () => {
	it('doubles and caps', () => {
		expect([0, 1, 2, 10].map(backoffMs)).toEqual([500, 1000, 2000, 30000]);
	});
});
