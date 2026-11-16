"""Exploratory event study: MW out by fuel around the largest 15-minute jump in load shed.

    uv run python -m analysis.event_study       # writes results/event_study/shed_vs_gas_outages.png
"""
import pandas as pd, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from kg.common import RESULTS, SOURCES
E=SOURCES/'ercot'/'data'; OUT=RESULTS/'event_study'
g=pd.read_excel(f'{E}/Data_for_Graphs_in_Board_Deck_-_Generation_Capacity_Out_by_Fuel_Type_Chart_and_Frequency_Chart.xlsx',sheet_name='GRAPH - GEN OUT BY FUEL TYPE')
g=g.loc[:,~g.columns.astype(str).str.startswith('Unnamed')].set_index('DATE'); g.index=pd.to_datetime(g.index)
g=g.rename(columns=lambda c:c.replace(' MW OUT TOTAL','').lower())[['ng','coal','nuclear','wind','solar']].rename(columns={'ng':'gas'})/1000  # GW
a=pd.read_excel(f'{E}/Available_Generation_and_Estimated_Load_without_Load_Shed_Data.xlsx')
shed=a[['DATE.2','LOAD SHED MW']].dropna().set_index('DATE.2')['LOAD SHED MW']; shed.index=pd.to_datetime(shed.index)
print('fuel series',g.index.min(),'..',g.index.max(),'step',g.index.to_series().diff().mode()[0],'| shed',shed.index.min(),'..',shed.index.max())
j=shed.diff(15); t0=j.idxmax(); print(f't0 (Central) = {t0}  shed jump 15min = {j.max():.0f} MW  shed at t0 = {shed[t0]:.0f}  (t0-15 = {shed[t0-pd.Timedelta("15min")]:.0f})')
print('top 5 15-min jumps:',{str(k):round(v) for k,v in j.nlargest(40).groupby(lambda x:x.floor('h')).max().nlargest(5).items()})
win=g.loc[t0-pd.Timedelta('12h'):t0+pd.Timedelta('12h')]; idx=win-win.loc[t0]
def excess(idx,pre_h,h,base=t0):
    pre=idx.loc[base-pd.Timedelta(f'{pre_h}h'):base]; x=(pre.index-base).total_seconds()/60
    slope=pd.Series({c:np.polyfit(x,pre[c],1)[0] for c in idx.columns})  # GW/min, OLS
    b=idx.loc[base]; return (idx.loc[base+pd.Timedelta(f'{h}h')]-b) - h*60*slope
print('\nMW out (GW) at t0-12h, t0, +1h, +3h, +6h:'); print(g.loc[[t0-pd.Timedelta('12h'),t0,t0+pd.Timedelta('1h'),t0+pd.Timedelta('3h'),t0+pd.Timedelta('6h')]].round(2))
res=[]
for pre_h in (12,6,3):
    for h in (1,3,6):
        e=excess(idx,pre_h,h); res.append(dict(pre_window_h=pre_h,horizon_h=h,gas=e.gas,coal=e.coal,nuclear=e.nuclear,wind=e.wind,DiD_vs_coal_nuc=e.gas-e[['coal','nuclear']].mean(),DiD_vs_coal=e.gas-e.coal))
r=pd.DataFrame(res).round(2); print('\nExcess beyond pre-trend (GW), base = t0:'); print(r.to_string(index=False))
# simultaneity check: base at t0+1h (after frequency trips settle)
b1=t0+pd.Timedelta('1h'); idx1=g.loc[t0-pd.Timedelta('12h'):t0+pd.Timedelta('13h')]
res=[]
for h in (2,5):
    e=excess(idx1,6,h,base=b1); res.append(dict(base='t0+1h',horizon_h=h,gas=e.gas,coal=e.coal,nuclear=e.nuclear,DiD_vs_coal_nuc=e.gas-e[['coal','nuclear']].mean()))
print('\nVariant, base t0+1h, pre-trend over the 6h before t0+1h:'); print(pd.DataFrame(res).round(2).to_string(index=False))
d=g.loc[t0-pd.Timedelta('30min'):t0+pd.Timedelta('30min')].diff().abs().sum(); print('\nGW of step changes within +/-30 min of t0:',d.round(2).to_dict())
nuc=g.nuclear.loc[t0:t0+pd.Timedelta('6h')]; print('nuclear steps after t0:',{str(k):round(v,2) for k,v in nuc.diff()[nuc.diff().abs()>0.1].items()})
fig,ax=plt.subplots(2,1,figsize=(10,7),sharex=True,gridspec_kw={'height_ratios':[3,1]})
for c,col in zip(['gas','coal','nuclear','wind'],['#c0392b','#555','#7d3cff','#2e86c1']): ax[0].plot((win.index-t0).total_seconds()/3600,idx[c],label=c,color=col,lw=1.6 if c=='gas' else 1.1)
ax[0].axvline(0,color='k',ls='--',lw=1); ax[0].set_ylabel('MW out, change vs t0 (GW)'); ax[0].legend(); ax[0].grid(alpha=.3)
ax[0].set_title(f'MW out by fuel around the biggest load-shed jump (t0 = {t0:%b %d %H:%M} Central)')
s=shed.loc[win.index.min():win.index.max()]/1000; ax[1].plot((s.index-t0).total_seconds()/3600,s,color='k'); ax[1].axvline(0,color='k',ls='--',lw=1); ax[1].set_ylabel('Load shed (GW)'); ax[1].set_xlabel('Hours from t0'); ax[1].grid(alpha=.3)
plt.tight_layout(); OUT.mkdir(parents=True,exist_ok=True); plt.savefig(OUT/'shed_vs_gas_outages.png',dpi=130); print('chart saved')
