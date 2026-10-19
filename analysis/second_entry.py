# 60거래일 안 N번째 테마 편입 다음 날 시가 매수 -> 5·10일 보유 성적. 실행: python analysis/second_entry.py (저장소 루트에서)
import csv, json, statistics as st, sys
sys.path.insert(0,'.')
from feed_export import load_code_map
H='analysis/'
ev=list(csv.DictReader(open(H+'theme_horizon/events.csv',encoding='utf-8-sig')))
days=json.load(open(H+'sheet_days.json',encoding='utf-8'))
bars=json.load(open(H+'cache/bars.json'))
shares=json.load(open(H+'cache/shares.json'))
cal=[b['d'] for b in bars['index']['KOSPI']]; pos={d:i for i,d in enumerate(cal)}
close={c:{b['d']:b['c'] for b in bs} for c,bs in bars['bars'].items()}
cm=load_code_map(); seen={}
for d,gs in days.items():
    if d not in pos: continue
    for g in gs:
        for s in g['stocks']:
            c=cm.get(s['name'])
            if c: seen.setdefault(c,set()).add(pos[d])
HOT={'전력기기','우주','반도체장비','PCB','로봇','반도체소부장','반도체','원자력'}
R=[]
for e in ev:
    i0=pos[e['day']]; h=seen.get(e['code'],set())
    p60=sum(1 for i in h if i0-60<=i<i0)
    sh=shares.get(e['code']); px=close.get(e['code'],{}).get(e['day'])
    mc=sh*px/1e8 if sh and px else None
    f=lambda k: float(e[k]) if e[k] else None
    R.append(dict(day=e['day'],n=p60+1,mc=mc,hot=e['head'] in HOT,o5=f('o2c5'),o10=f('o2c10'),x10=f('ex10'),dn=f('max_dn10'),c10=f('c2c10'),streak=int(e['streak'])))
def line(name,rs):
    for k,lab in (('o5','D+5'),('o10','D+10')):
        v=[r[k] for r in rs if r[k] is not None]
        if len(v)<15: print(f'{name:34s} {lab} 표본{len(v)}'); continue
        print(f"{name:34s} {lab} n={len(v):4d} 평균 {st.fmean(v):+6.2f}% 중앙 {st.median(v):+6.2f}% 승률 {sum(x>0 for x in v)/len(v)*100:4.0f}% +10%↑ {sum(x>=10 for x in v)/len(v)*100:3.0f}% -10%↓ {sum(x<=-10 for x in v)/len(v)*100:3.0f}%")
    x=[r['x10'] for r in rs if r['x10'] is not None]
    if x: print(f"{'':34s} 지수대비초과 D+10 평균 {st.fmean(x):+.2f}%")
print('== 60거래일 안 몇 번째 편입인가 (다음날 시가 매수)')
for n,lab in ((1,'1번째(처음)'),(2,'2번째'),(3,'3번째'),(4,'4~5번째')):
    line(lab,[r for r in R if (r['n']==n if n<4 else 4<=r['n']<=5)])
line('6번째+',[r for r in R if r['n']>=6])
line('전체',R)
two=[r for r in R if r['n']==2]
print('== 2번째 편입 + 필터')
line('2번째 & 시총3천억+',[r for r in two if r['mc'] and r['mc']>=3000])
line('2번째 & 주도테마',[r for r in two if r['hot']])
line('2번째 & 시총3천억+ & 주도테마',[r for r in two if r['hot'] and r['mc'] and r['mc']>=3000])
line('2번째 & 1500억 미만',[r for r in two if r['mc'] and r['mc']<1500])
print('== 2번째 편입, 기간별 (안정성)')
for a,b in (('2025-06','2025-12'),('2026-01','2026-06'),('2026-07','2026-12')):
    line(f'{a}~{b}',[r for r in two if a<=r['day'][:7]<=b])
print('== 연속 2일차 테마(참고)')
line('테마 2일차',[r for r in R if r['streak']==2])
print('== 그날까지 알 수 있던 정보로만 정한 주도테마')
# heads per event
for r,e in zip(R,ev): r['head']=e['head']; r['i0']=pos[e['day']]
byhead={}
for r in R: byhead.setdefault(r['head'],[]).append(r)
def trailing(r):
    # 같은 테마의 과거 편입 중 이미 D+10 결과가 나온 것(10거래일 이상 지난 것), 최근 120거래일
    past=[x['c10'] for x in byhead[r['head']] if r['i0']-120<=x['i0']<=r['i0']-10 and x['c10'] is not None]
    return (st.fmean(past),len(past)) if len(past)>=5 else (None,len(past))
def freq(r):
    return sum(1 for x in byhead[r['head']] if r['i0']-20<=x['i0']<r['i0'])
for r in R:
    r['tr'],r['trn']=trailing(r); r['fq']=freq(r)
line('2번째 & 테마 과거성적 +',[r for r in two if r['tr'] is not None and r['tr']>0])
line('2번째 & 테마 과거성적 -',[r for r in two if r['tr'] is not None and r['tr']<=0])
line('2번째 & 테마 직전20일 10건+',[r for r in two if r['fq']>=10])
line('2번째 & 테마 직전20일 10건 미만',[r for r in two if r['fq']<10])
line('2번째 & 과거성적+ & 시총3천억+',[r for r in two if r['tr'] is not None and r['tr']>0 and r['mc'] and r['mc']>=3000])
line('전체 & 테마 과거성적 +',[r for r in R if r['tr'] is not None and r['tr']>0])
print('== 안정성: 2번째 & 테마 직전20일 10건+')
hot2=[r for r in two if r['fq']>=10]
for a,b in (('2025-06','2025-12'),('2026-01','2026-06'),('2026-07','2026-12')):
    line(f'{a}~{b}',[r for r in hot2 if a<=r['day'][:7]<=b])
line('+ 시총3천억+',[r for r in hot2 if r['mc'] and r['mc']>=3000])
line('전체 & 테마 직전20일 10건+',[r for r in R if r['fq']>=10])
line('1번째 & 테마 직전20일 10건+',[r for r in R if r['fq']>=10 and r['n']==1])
from collections import Counter
print(Counter(r['head'] for r in hot2).most_common(10))
