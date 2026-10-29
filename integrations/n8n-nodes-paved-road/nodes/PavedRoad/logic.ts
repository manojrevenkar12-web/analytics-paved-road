/** Request building and response handling, kept free of n8n so it can be tested directly. */

export type Operation = 'query' | 'ask' | 'freshness';

export interface GatewayResult {
	status: 'ok' | 'refused' | 'error';
	rule?: string | null;
	message?: string;
	columns?: string[];
	rows?: unknown[][];
	truncated?: boolean;
	lane?: string | null;
	cached?: boolean;
	snapshots?: Record<string, number | null>;
}

export interface Product {
	name: string;
	table: string;
	data_through: string | null;
	freshness: { expected_by: string };
	owner: { team: string; contact: string };
}

export class GatewayRefusal extends Error {
	constructor(
		public rule: string,
		message: string,
		public retryable: boolean,
	) {
		super(message);
	}
}

/** Rows as n8n items: one JSON object per row, keyed by column name. */
export function rowsToItems(r: GatewayResult): Record<string, unknown>[] {
	const cols = r.columns ?? [];
	return (r.rows ?? []).map((row) => Object.fromEntries(cols.map((c, i) => [c, row[i]])));
}

/** A refusal is a workflow error with the gateway's message; 429/503 are retryable. */
export function check(r: GatewayResult, httpStatus: number): GatewayResult {
	if (r.status === 'ok') return r;
	const retryable = httpStatus === 429 || httpStatus === 503;
	throw new GatewayRefusal(r.rule ?? 'error', r.message ?? 'The gateway refused the query.', retryable);
}

/** Products whose newest data is older than the day before `today` (UTC), i.e. late. */
export function lateProducts(products: Product[], today: string): Product[] {
	const yesterday = new Date(`${today}T00:00:00Z`);
	yesterday.setUTCDate(yesterday.getUTCDate() - 1);
	const want = yesterday.toISOString().slice(0, 10);
	return products.filter((p) => !p.data_through || p.data_through < want);
}

export function backoffMs(attempt: number): number {
	return Math.min(30_000, 500 * 2 ** attempt);
}
