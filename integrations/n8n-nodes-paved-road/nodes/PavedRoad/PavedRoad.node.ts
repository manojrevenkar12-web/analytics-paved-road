import type {
	IDataObject,
	IExecuteFunctions,
	IHttpRequestOptions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';
import { NodeConnectionTypes, NodeOperationError } from 'n8n-workflow';

import { backoffMs, check, GatewayRefusal, type GatewayResult, lateProducts, type Product, rowsToItems } from './logic';

export class PavedRoad implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Analytics Paved Road',
		name: 'pavedRoad',
		icon: 'fa:database',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Governed SQL, metric questions and freshness checks through the query gateway',
		defaults: { name: 'Analytics Paved Road' },
		inputs: [NodeConnectionTypes.Main],
		outputs: [NodeConnectionTypes.Main],
		credentials: [{ name: 'pavedRoadApi', required: true }],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				options: [
					{ name: 'Run SQL', value: 'query', action: 'Run SQL through the gateway' },
					{ name: 'Ask a Question', value: 'ask', action: 'Ask a metric question' },
					{ name: 'Check Freshness', value: 'freshness', action: 'List data products that are late' },
				],
				default: 'query',
			},
			{
				displayName: 'SQL',
				name: 'sql',
				type: 'string',
				typeOptions: { rows: 5 },
				default: '',
				displayOptions: { show: { operation: ['query'] } },
				description: 'Spark SQL; the gateway applies your role, partition rules and limits',
			},
			{
				displayName: 'Question',
				name: 'question',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['ask'] } },
			},
			{
				displayName: 'Retries on 429/503',
				name: 'retries',
				type: 'number',
				default: 3,
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const out: INodeExecutionData[] = [];
		const creds = await this.getCredentials('pavedRoadApi');
		const base = String(creds.baseUrl).replace(/\/$/, '');
		for (let i = 0; i < items.length; i++) {
			const op = this.getNodeParameter('operation', i) as string;
			const retries = this.getNodeParameter('retries', i, 3) as number;
			const call = async (opts: IHttpRequestOptions) => {
				for (let attempt = 0; ; attempt++) {
					const res = await this.helpers.httpRequestWithAuthentication.call(this, 'pavedRoadApi', {
						...opts,
						returnFullResponse: true,
						ignoreHttpStatusErrors: true,
						json: true,
					});
					const status = (res as { statusCode: number }).statusCode;
					const body = (res as { body: unknown }).body;
					if ((status === 429 || status === 503) && attempt < retries) {
						await new Promise((r) => setTimeout(r, backoffMs(attempt)));
						continue;
					}
					return { status, body };
				}
			};
			try {
				if (op === 'query') {
					const sql = this.getNodeParameter('sql', i) as string;
					const { status, body } = await call({ method: 'POST', url: `${base}/api/query`, body: { sql } });
					const r = check(body as GatewayResult, status);
					for (const row of rowsToItems(r)) out.push({ json: row as IDataObject, pairedItem: { item: i } });
				} else if (op === 'ask') {
					const question = this.getNodeParameter('question', i) as string;
					const { body } = await call({ method: 'POST', url: `${base}/api/ask`, body: { question } });
					out.push({ json: body as IDataObject, pairedItem: { item: i } });
				} else {
					const { body } = await call({ method: 'GET', url: `${base}/api/catalog` });
					const today = new Date().toISOString().slice(0, 10);
					for (const p of lateProducts(body as Product[], today))
						out.push({ json: p as unknown as IDataObject, pairedItem: { item: i } });
				}
			} catch (e) {
				if (this.continueOnFail()) {
					out.push({ json: { error: (e as Error).message, rule: (e as GatewayRefusal).rule }, pairedItem: { item: i } });
					continue;
				}
				throw new NodeOperationError(this.getNode(), (e as Error).message, { itemIndex: i });
			}
		}
		return [out];
	}
}
