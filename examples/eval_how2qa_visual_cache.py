"""Offline Raw-vs-RRF How2QA visual retrieval using cached CLIP frame embeddings."""
from __future__ import annotations
import argparse,csv,json,re
from collections import defaultdict
from pathlib import Path
import numpy as np, torch
from sentence_transformers import SentenceTransformer
STOP={'what','who','whom','whose','where','why','how','when','which','is','are','was','were','do','does','did','the','a','an','of','in','on','at','to','for','with','and','or','this','that'}
def rewrite(q): return ' '.join(w for w in re.findall(r"[A-Za-z']+",q) if w.lower() not in STOP) or q
def rank_rrf(scores):
 f=np.zeros(scores.shape[1]);
 for s in scores:
  for rank,i in enumerate(np.argsort(-s),1): f[i]+=1/(60+rank)
 return np.argsort(-f)
def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',default='data/how2qa/val_local_manifest.csv');p.add_argument('--cache',default='data/how2qa/clip_cache');p.add_argument('--limit-videos',type=int,default=100);p.add_argument('--out',default='results/how2qa_visual_cache_pilot.json');a=p.parse_args();root=Path.cwd();g=defaultdict(list)
 with open(a.manifest,newline='',encoding='utf-8') as f:
  for r in csv.DictReader(f):
   if r['available']=='1' and (root/a.cache/f"{r['video_id']}.npz").exists():g[r['video_id']].append(r)
 ids=sorted(g)[:a.limit_videos];m=SentenceTransformer('clip-ViT-B-32',device='cuda' if torch.cuda.is_available() else 'cpu');rows={'raw':[],'rrf':[]}
 for vid in ids:
  d=np.load(root/a.cache/f'{vid}.npz'); e,t=d['embeddings'],d['timestamps']; qs=[r['question'] for r in g[vid]]; raw=m.encode(qs,normalize_embeddings=True,convert_to_numpy=True); allq=[x for q in qs for x in (q,rewrite(q),'a video showing '+rewrite(q))]; multi=m.encode(allq,normalize_embeddings=True,convert_to_numpy=True)
  for r,x,z in zip(g[vid],raw,[multi[i*3:(i+1)*3] for i in range(len(qs))]):
   for name,order in [('raw',np.argsort(-(x@e.T))),('rrf',rank_rrf(z@e.T))]:
    s,en=float(r['start_s']),float(r['end_s']);ts=t[order];rows[name].append({f'r{k}':any(s<=v<=en for v in ts[:k]) for k in(1,5,8,20)})
 out={'videos':len(ids),'questions':len(rows['raw'])}
 for name,rs in rows.items():out[name]={f'recall_at_{k}':float(np.mean([r[f'r{k}'] for r in rs])) for k in(1,5,8,20)}
 Path(a.out).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
