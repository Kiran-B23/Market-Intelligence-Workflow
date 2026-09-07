import type { BreakingChangeAffectedWorkflow, BreakingChangeRecommendation } from '@n8n/api-types';
import { BreakingChangeCategory } from '../../types';

@BreakingChangeRule({ version: 'v2' })
export class RemovedNodesRule implements IBreakingChangeWorkflowRule {
	private readonly REMOVED_NODES = [
		'n8n-nodes-base.spontit',
		'n8n-nodes-base.crowdDev',
		'n8n-nodes-base.kitemaker',
	];

	id: string = 'removed-nodes-v2';

	getMetadata(): BreakingChangeRuleMetadata {
		return {
			version: 'v2',
			title: 'Removed Deprecated Nodes',
			description: 'Several deprecated nodes have been removed and will no longer work',
			category: BreakingChangeCategory.workflow,
			severity: 'low',
			documentationUrl:
				'https://docs.n8n.io/2-0-breaking-changes/#removed-nodes-for-retired-services',
		};
	}

	async getRecommendations(): Promise<BreakingChangeRecommendation[]> {
		return [
			{
				action: 'Update affected workflows',
				description: 'Replace removed nodes with their updated versions or alternatives',
			},
		];
	}
}
