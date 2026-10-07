"""Optional report plot: complete-process summary latency from measured raw samples."""
import argparse
import csv
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def plot(run, output):
    metadata = json.loads((run/'metadata.json').read_text())
    if metadata['status'] != 'complete' or output.exists():
        raise ValueError('Require a complete run and a new output filename')
    with (run/'runs.csv').open() as source:
        measured = [r for r in csv.DictReader(source) if r['phase']=='measured']
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True, layout='constrained')
    colors = {'row':'#8c564b', 'column':'#3366aa', 'pruned':'#008877', 'duckdb':'#aa6611'}
    for ax, contract in zip(axes, ('ESZ26', 'NQZ26')):
        specs = [w for w in metadata['workloads'] if w['contract']==contract and w['kind']=='summary']
        total = metadata['contracts'][contract]['full_text_decimal_summary'][0]['record_count']
        specs.sort(key=lambda w: w['selected_records'])
        x = [100*w['selected_records']/total for w in specs]
        for (variant, color), marker in zip(colors.items(), 'os^D'):
            values = [[1000*float(r['process_seconds']) for r in measured
                       if r['workload']==w['id'] and r['variant']==variant] for w in specs]
            if any(len(v)!=metadata['repetitions'] for v in values):
                raise ValueError('Sample inventory differs from metadata')
            medians = [statistics.median(v) for v in values]
            quartiles = [statistics.quantiles(v,n=4,method='inclusive') for v in values]
            errors = [[m-q[0] for m,q in zip(medians,quartiles)],
                      [q[2]-m for m,q in zip(medians,quartiles)]]
            ax.errorbar(x, medians, yerr=errors, marker=marker, capsize=3,
                        color=color, label=variant, linewidth=1.5)
        ax.set(xscale='log', yscale='log', xlabel='Selected records (% of complete history)', title=contract)
        ax.grid(alpha=.2, which='both')
    axes[0].set_ylabel('Complete-process latency (ms, log scale)')
    axes[1].legend(frameon=False, loc='lower right')
    fig.suptitle('Summary queries: median and interquartile range')
    fig.supxlabel('Three observed UTC windows; fresh processes; OS cache uncontrolled', fontsize=9)
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def plot_scale(run, output):
    series = json.loads((run/'series.json').read_text())
    if series['status']!='complete' or output.exists():
        raise ValueError('Require a complete series and a new output filename')
    inventory = []
    for count in sorted(series['sizes']):
        directory = run/str(count)
        metadata = json.loads((directory/'metadata.json').read_text())
        with (directory/'runs.csv').open() as source:
            records = [r for r in csv.DictReader(source) if r['phase']=='measured']
        inventory.append((count, metadata, records))
    fig, axes = plt.subplots(2, 3, figsize=(12, 7), layout='constrained')
    colors = {'row':'#8c564b', 'column':'#3366aa', 'pruned':'#008877', 'duckdb':'#aa6611'}
    for row, window in enumerate(('full', 'prefix_1pct')):
        for column, kind in enumerate(('summary', 'flow', 'bars')):
            ax = axes[row, column]
            for (variant, color), marker in zip(colors.items(), 'os^D'):
                values = []
                for count, metadata, records in inventory:
                    spec = next(w for w in metadata['workloads'] if w['window']==window and w['kind']==kind)
                    samples = [float(r['process_seconds']) for r in records
                               if r['workload']==spec['id'] and r['variant']==variant]
                    if metadata['status']!='complete' or len(samples)!=metadata['repetitions']:
                        raise ValueError('Incomplete scale sample inventory')
                    values.append(samples)
                medians = [statistics.median(v) for v in values]
                quartiles = [statistics.quantiles(v, n=4, method='inclusive') for v in values]
                ax.errorbar([n for n,_,_ in inventory], medians,
                    yerr=[[m-q[0] for m,q in zip(medians, quartiles)],
                          [q[2]-m for m,q in zip(medians, quartiles)]],
                    marker=marker, color=color, label=variant, capsize=3)
            ax.set(xscale='log', yscale='log', title=f'{kind} / {"100%" if row==0 else "1%"} selected',
                   xlabel='Synthetic records', ylabel='Complete-process seconds')
            ax.grid(alpha=.2, which='both')
    axes[0, 0].legend(frameon=False)
    fig.suptitle('Synthetic ESZ26: median and interquartile range')
    fig.supxlabel('Fresh processes; native resident-memory measurement overhead included; OS cache uncontrolled', fontsize=9)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--series', action='store_true', help='Plot a synthetic size-series report')
    args = p.parse_args()
    (plot_scale if args.series else plot)(args.run, args.output)
