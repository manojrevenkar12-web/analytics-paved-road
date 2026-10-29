import type { IAuthenticateGeneric, ICredentialTestRequest, ICredentialType, INodeProperties } from 'n8n-workflow';

export class PavedRoadApi implements ICredentialType {
	name = 'pavedRoadApi';
	displayName = 'Analytics Paved Road API';
	documentationUrl = 'https://github.com/manojrevenkar12-web/analytics-paved-road/blob/main/docs/ACCESS.md';
	properties: INodeProperties[] = [
		{ displayName: 'Gateway URL', name: 'baseUrl', type: 'string', default: 'http://apr-gateway.analytics.svc:8000' },
		{
			displayName: 'Service User',
			name: 'user',
			type: 'string',
			default: 'n8n',
			description: 'The automation user the gateway knows this workflow as; its role decides what it may read',
		},
		{ displayName: 'Token', name: 'token', type: 'string', typeOptions: { password: true }, default: '' },
	];
	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: { 'X-User': '={{$credentials.user}}', Authorization: '=Bearer {{$credentials.token}}' },
		},
	};
	test: ICredentialTestRequest = { request: { baseURL: '={{$credentials.baseUrl}}', url: '/api/me' } };
}
