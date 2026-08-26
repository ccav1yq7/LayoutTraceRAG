"""One-time sequential frame decode + CLIP image-embedding cache for How2QA."""
from __future__ import annotations
import argparse, csv
from collections import defaultdict
from pathlib import Path
import cv2, numpy as np, torch
from PIL import Image
from sentence_transformers import SentenceTransformer


def frames(path: Path, every: float):
    cap=cv2.VideoCapture(str(path)); fps=cap.get(cv2.CAP_PROP_FPS) or 25.; step=max(1,round(fps*every)); out=[]; ts=[]; i=0
    while cap.grab():
        if i % step == 0:
            ok, frame=cap.retrieve()
            if ok:
                out.append(Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))); ts.append(i/fps)
        i+=1
    cap.release(); return out,ts


def main():
    p=argparse.ArgumentParser(); p.add_argument('--manifest',default='data/how2qa/val_local_manifest.csv'); p.add_argument('--out-dir',default='data/how2qa/clip_cache'); p.add_argument('--frame-every',type=float,default=2.0); p.add_argument('--batch-size',type=int,default=128); a=p.parse_args()
    root=Path.cwd(); groups=defaultdict(list)
    with open(a.manifest,newline='',encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['available']=='1': groups[r['video_id']].append(r)
    out=root/a.out_dir; out.mkdir(parents=True,exist_ok=True); model=SentenceTransformer('clip-ViT-B-32',device='cuda' if torch.cuda.is_available() else 'cpu')
    skipped=[]
    for n,(vid,rows) in enumerate(sorted(groups.items()),1):
        target=out/f'{vid}.npz'
        if target.exists(): continue
        imgs,timestamps=frames(root/rows[0]['local_video'],a.frame_every)
        if not imgs: skipped.append(vid); continue
        emb=model.encode(imgs,batch_size=a.batch_size,show_progress_bar=False,normalize_embeddings=True,convert_to_numpy=True)
        np.savez_compressed(target,embeddings=emb,timestamps=np.asarray(timestamps,dtype=np.float32))
        if n%25==0: print(f'cached={n}/{len(groups)} skipped={len(skipped)}',flush=True)
    (out/'skipped.txt').write_text('\n'.join(skipped)+'\n')

if __name__=='__main__': main()
