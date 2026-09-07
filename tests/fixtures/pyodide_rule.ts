@BreakingChangeRule({ version: 'v2' })
export class PyodideRemovedRule implements IBreakingChangeWorkflowRule {
	id: string = 'pyodide-removed-v2';

	getMetadata(): BreakingChangeRuleMetadata {
		return {
			version: 'v2',
			title: 'Remove Pyodide-based Python in Code node',
			description:
				'The Pyodide-based Python implementation in the Code node has been removed and replaced with a native Python task runner implementation',
			category: BreakingChangeCategory.workflow,
			severity: 'medium',
			documentationUrl:
				'https://docs.n8n.io/2-0-breaking-changes/#remove-pyodide-based-python-code-node',
		};
	}

	async getRecommendations(): Promise<BreakingChangeRecommendation[]> {
		return [
			{
				action: 'Update Code nodes to use native Python',
				description: 'Manually update affected Code nodes from the legacy python parameter',
			},
		];
	}
}
