"""Atomic JSON/Markdown artifacts, safe controlled failure reasons, no credentials."""
import json
import os
from pathlib import Path


def write_report(directory,report):
    from tools.output_safety import protect_generated
    report = protect_generated(report)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    rows=['# Diagnostic quality evaluation','',f"Status: **{report['status']}**",'',
          f"Real model execution: {report.get('real_model_execution',False)}",'',
          f"Selected held-out cases: {len(report.get('selected_ids',[]))}",
          f"Scored cases: {len([c for c in report.get('cases',[]) if c.get('metrics')])}",'',
          '## Metrics','', '```json',json.dumps(report.get('metrics'),indent=2),'```','',
          '## Usage and budget','', '```json',json.dumps(report.get('usage',{}),indent=2),'```','',
          '## Dataset independence','',str(report.get('dataset_audit',{}).get('warning') or 'See JSON audit.'),'',
          '## Execution notes','',*report.get('notes',[]),'',
          'Automated quality scores are proxies. No fix effectiveness is established.']
    for name,content in [('evaluation.json',json.dumps(report,indent=2,sort_keys=True)+'\n'),('evaluation.md','\n'.join(rows)+'\n')]:
        temporary=directory/(name+'.tmp');temporary.write_text(content);os.chmod(temporary,0o600);temporary.replace(directory/name)
