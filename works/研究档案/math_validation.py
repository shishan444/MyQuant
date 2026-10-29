# -*- coding: utf-8 -*-
"""系统外数学算法离线验证（乙轨）——三目标：风控锁利 / 新信号源 / 触发精度。
数据: data/market/BTCUSDT_4h.parquet（真实行情）。无前视纪律：全部 trailing 窗口，
信号 t 收盘确认按 t+1 收盘执行口径计收益。费率 0.15%/边（引擎默认费+滑近似）。
"""
import numpy as np, pandas as pd

FEE = 0.0015
df = pd.read_parquet('data/market/BTCUSDT_4h.parquet').reset_index(drop=True)
o,h,l,c,v = (df[k].astype(float).values for k in ['open','high','low','close','volume'])
n = len(c); ts_full = pd.read_parquet('data/market/BTCUSDT_4h.parquet').index
ts = pd.to_datetime(pd.Series(ts_full))
ret1 = np.diff(np.log(c), prepend=np.log(c[0]))

SEG = [('DEV','2024-01-01','2025-02-01'),('OOS1深熊','2025-02-01','2025-05-01'),
       ('OOS2爬升','2025-05-01','2026-02-01'),('OOS3反弹','2026-02-01','2026-05-01'),
       ('OOS4阴跌','2026-05-01','2026-08-01')]

# ---------- 数学算法库 ----------
def kalman(pr, q=0.01, r=25.0):
    x = np.zeros((n,2)); P = np.zeros((n,2,2)); x[0]=[pr[0],0]; P[0]=np.eye(2)*100
    F=np.array([[1.,1.],[0.,1.]]); H=np.array([1.,0.])
    for t in range(1,n):
        xp=F@x[t-1]; Pp=F@P[t-1]@F.T+np.diag([q,q])
        S=H@Pp@H+r; K=(Pp@H)/S
        x[t]=xp+K*(pr[t]-H@xp); P[t]=(np.eye(2)-np.outer(K,H))@Pp
    return x  # [:,0]=平滑水平 [:,1]=斜率

def ema(s, span):
    a=2/(span+1); out=np.empty_like(s); out[0]=s[0]
    for t in range(1,len(s)): out[t]=a*s[t]+(1-a)*out[t-1]
    return out

def lowfreq_causal(pr, level=3):
    """小波低频近似（级联 trailing 均值, 窗 3/5/9）——因果版多分辨率，含滞后代价。"""
    s=pd.Series(pr)
    for j in range(level):
        s=s.rolling(2**(j+1)+1, min_periods=1).mean()
    return s.values

def rolling_q(a, win, q):
    return pd.Series(a).rolling(win, min_periods=1).quantile(q).values

KX = kalman(c)
kf_level, kf_slope = KX[:,0], KX[:,1]
kf_resid = c - kf_level
kf_vol = ema(np.abs(kf_resid)/c, 20)                    # 卡尔曼残差 EWMA 波动
lf = lowfreq_causal(c, 3)                              # 小波低频线
wt_resid = c - lf
wt_vol = pd.Series(np.abs(wt_resid)/c).rolling(20,1).mean().values
park = np.sqrt(pd.Series(np.log(h/np.maximum(l,1e-9))**2).rolling(20,1).mean().values)
gk_term = 0.5*np.log(h/np.maximum(l,1e-9))**2-(2*np.log(2)-1)*np.log(np.maximum(c,1e-9)/np.maximum(o,1e-9))**2
gk = np.sqrt(pd.Series(np.maximum(gk_term,0)).rolling(20,1).mean().values)
q75 = rolling_q(np.abs(ret1), 20, 0.75)
ewma_vol = ema(np.abs(ret1), 20)

def hurst_rs(pr, win=100):
    out=np.full(n,np.nan); lr=np.diff(np.log(pr))
    for t in range(win+1,n):
        w=lr[t-win:t]; s=w.std()
        if s>1e-12:
            z=np.cumsum(w-w.mean()); out[t]=np.log((z.max()-z.min())/s)/np.log(win)
    return out
H = hurst_rs(c)

# ---------- 通用回测（做多状态机：进场→离场/止损/max_hold；追踪波动线=锁利润语义） ----------
def bt_long(entry_sig, exit_sig, vol=None, k=2.0, max_hold=24, fixed_pct=None):
    pos=np.zeros(n); hold=0; peak=0.0; entry=0.0
    for t in range(260,n):
        if pos[t-1]==0 and entry_sig[t]:
            pos[t]=1; hold=1; entry=c[t]; peak=c[t]
        elif pos[t-1]==1:
            peak=max(peak,c[t]); hit=False
            if fixed_pct is not None: hit = c[t] < entry*(1-fixed_pct)
            elif vol is not None and not np.isnan(vol[t]): hit = c[t] < peak*(1-k*vol[t])
            if exit_sig[t] or hold>=max_hold or hit: pos[t]=0; hold=0
            else: pos[t]=1; hold+=1
    rets=np.concatenate([[0],np.diff(np.log(c))])
    pnl=np.concatenate([[0],pos[:-1]])*rets          # t-1 收盘信号 → t 持仓吃 t 收益（无前视）
    turns=np.abs(np.diff(np.concatenate([[0],pos])))
    return pnl-turns*FEE

def seg_table(pnl):
    return [pnl[(ts>=pd.Timestamp(s, tz='UTC'))&(ts<pd.Timestamp(e, tz='UTC'))].sum()*100 for _,s,e in SEG]

# ---------- PART 1 风控锁利：种子5信号 × 7 种止损 ----------
win_u = pd.Series(c).rolling(20,1).mean()+2*pd.Series(c).rolling(20,1).std()
win_l = pd.Series(c).rolling(20,1).mean()-2*pd.Series(c).rolling(20,1).std()
entry5 = np.concatenate([[False],(c[1:]<win_l.values[1:])&(c[:-1]>=win_l.values[:-1])])  # first_true 近似
exit5 = c>win_u.values

print('='*80); print('PART1 风控锁利：种子5（BB下轨买上轨卖, max_hold=24）× 数学动态止损（追踪线 k=2）')
print(f"{'止损算法':<14}{'DEV':>8}{'OOS1':>8}{'OOS2':>8}{'OOS3':>8}{'OOS4':>8}{'合计':>8}")
pnl0 = bt_long(entry5, exit5, fixed_pct=0.05)
seg0 = seg_table(pnl0)
print(f"{'固定5%基线':<14}"+''.join(f"{x:>8.2f}" for x in seg0)+f"{sum(seg0):>8.2f}")
for name,vol in [('卡尔曼残差vol',kf_vol),('小波残差vol',wt_vol),('Parkinson',park),
                 ('Garman-Klass',gk),('滚动分位p75',q75),('EWMA',ewma_vol)]:
    pnl = bt_long(entry5, exit5, vol=vol, k=2.0)
    seg = seg_table(pnl)
    print(f"{name:<14}"+''.join(f"{x:>8.2f}" for x in seg)+f"{sum(seg):>8.2f}")

# ---------- PART 2 新信号源：纯数学信号的前向收益（样本外分段） ----------
def fwd_stats(sig, hb=12):
    ev = np.where((sig[1:].astype(bool))&(~sig[:-1].astype(bool)))[0]+1  # 上升沿→次bar
    ev = ev[(ev>260)&(ev+hb<n)]
    if len(ev)==0: return None
    fr = np.log(c[ev+hb]/c[ev])
    return ev, fr
sig_defs = [
    ('卡尔曼斜率转正多', kf_slope>0),
    ('卡尔曼斜率转负空', kf_slope<0),
    ('小波低频转向上多', np.concatenate([[False],np.diff(lf)>0])),
    ('卷积残差z<-2超卖多', (c-lf)/np.maximum(pd.Series(wt_resid).rolling(50,1).std().values,1e-9) < -2),
    ('Hurst>0.55趋势多', (H>0.55)&(c>lf)),
]
print('='*80); print('PART2 新信号源：纯数学信号事件前向收益%（fwd12/fwd24，样本外分段胜率）')
base12 = np.nanmean(np.log(c[12:]/c[:-12]))*100
print(f"{'信号':<18}{'事件数':>6}{'全窗fwd12%':>10}{'胜率':>7}{'fwd24%':>8}  OOS分段fwd12均值%")
for name,sig in sig_defs:
    r12=fwd_stats(sig,12); r24=fwd_stats(sig,24)
    if r12 is None: print(f'{name:<18}  零事件'); continue
    ev,fr=r12; _,fr24=r24
    line=f"{name:<18}{len(ev):>6}{fr.mean()*100:>10.2f}{(fr>0).mean()*100:>6.0f}%{fr24.mean()*100:>8.2f}"
    parts=[]
    for _,s,e in SEG[1:]:
        m=(ts.iloc[ev]>=pd.Timestamp(s, tz='UTC'))&(ts.iloc[ev]<pd.Timestamp(e, tz='UTC'))
        parts.append(f"{fr[m].mean()*100:+.2f}({m.sum()})" if m.sum()>0 else '—')
    print(line+'  '+' '.join(parts))
print(f"（对照：无条件全窗 fwd12 基线 {base12:+.2f}%）")

# ---------- PART 3 触发精度：同信号三种触发源 ----------
print('='*80); print('PART3 触发精度：BB下轨触发——原始价 vs 卡尔曼平滑价 vs 小波低频价')
trig_defs=[('原始close', c), ('卡尔曼平滑价', kf_level), ('小波低频价', lf)]
print(f"{'触发源':<12}{'事件数':>6}{'fwd12%':>8}{'假信号率':>9}{'fwd24%':>8}{'OOS假信号率':>12}")
for name,src in trig_defs:
    wu=pd.Series(src).rolling(20,1).mean()+2*pd.Series(src).rolling(20,1).std()
    wl=pd.Series(src).rolling(20,1).mean()-2*pd.Series(src).rolling(20,1).std()
    sig=np.concatenate([[False],(src[1:]<wl.values[1:])])
    r=fwd_stats(sig,12)
    if r is None: print(f'{name:<12} 零事件'); continue
    ev,fr=r; _,fr24=fwd_stats(sig,24)
    oosm=(ts.iloc[ev]>=pd.Timestamp('2025-02-01', tz='UTC'))
    print(f"{name:<12}{len(ev):>6}{fr.mean()*100:>8.2f}{(fr<0).mean()*100:>8.0f}%{fr24.mean()*100:>8.2f}{(fr[oosm]<0).mean()*100:>11.0f}%")
print('（GARCH 以 EWMA 代理；小波为因果级联均值近似——两者如实标注非精确版）')
