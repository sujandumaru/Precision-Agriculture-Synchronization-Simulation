#!/usr/bin/env python3
"""Generate manuscript figures from the full-design prototype sweep."""
from __future__ import annotations
import argparse, csv, glob, os
from collections import defaultdict
from statistics import mean
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42

ORDER = ["last_write_wins","cloud_preferred","display_preferred",
         "version_vector_causal","version_vector_cloud_wins","version_vector_display_wins",
         "version_vector_random","crdt_field_merge","manual_review_all","domain_aware"]
LABEL = {"last_write_wins":"Last-write-wins","cloud_preferred":"Cloud-preferred",
         "display_preferred":"Display-preferred","version_vector_causal":"Causal, node-order",
         "version_vector_cloud_wins":"Causal, cloud-authority",
         "version_vector_display_wins":"Causal, display-authority",
         "version_vector_random":"Causal, pseudo-random",
         "crdt_field_merge":"Field-wise register merge","manual_review_all":"Manual-review-all",
         "domain_aware":"Domain-aware"}

def save(fig, out, stem):
    """Write both a vector PDF and a 600 dpi raster.

    The PDF is the submission copy: as vector line art it has no effective
    resolution, so it cannot fall below a journal's dpi floor at any placed
    width. The PNG is kept for previewing and for tools that will not take a
    PDF, and is byte-identical to the figure embedded in the manuscript.
    """
    fig.tight_layout()
    fig.savefig(f"{out}/{stem}.pdf")
    fig.savefig(f"{out}/{stem}.png", dpi=600)
    plt.close(fig)


def load(p):
    rows=[]
    for f in glob.glob(os.path.join(p,"prototype_runs*.csv")): rows+=list(csv.DictReader(open(f)))
    return rows

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--prototype",required=True)
    ap.add_argument("--out",default="figures"); a=ap.parse_args()
    os.makedirs(a.out,exist_ok=True); rows=load(a.prototype)

    P=defaultdict(lambda: defaultdict(list))
    for r in rows:
        P[r["policy"]]["hr"].append(float(r["high_risk_silent_overwrites"]))
        P[r["policy"]]["mr"].append(float(r["manual_reviews"]))

    # ---- Fig 2: protection-burden frontier
    pts=[(p,mean(P[p]["mr"]),mean(P[p]["hr"])) for p in ORDER if p in P]
    groups=[]
    for p,x,y in pts:
        for g in groups:
            if abs(g["x"]-x)<0.05 and abs(g["y"]-y)<0.05:
                g["names"].append(LABEL[p]); break
        else:
            groups.append({"x":x,"y":y,"names":[LABEL[p]],
                           "star":p=="domain_aware"})
    fig,ax=plt.subplots(figsize=(7.4,5.0))
    for g in groups:
        star=g["star"]
        ax.scatter(g["x"],g["y"],s=230 if star else 110,marker="*" if star else "o",
                   color="#C0392B" if star else "#2C3E50",zorder=3,
                   edgecolors="white",linewidths=0.9)
        txt="\n".join(g["names"])+f"\n({g['x']:.2f}, {g['y']:.2f})"
        if len(g["names"])>1:
            dx,dy,ha=14,0,"left"
        elif star:
            dx,dy,ha=0,-46,"center"
        elif g["x"]>6:
            dx,dy,ha=-12,14,"right"
        else:
            dx,dy,ha=14,4,"left"
        ax.annotate(txt,(g["x"],g["y"]),textcoords="offset points",
                    xytext=(dx,dy),fontsize=8.4,ha=ha,va="center",linespacing=1.35)
    ax.set_xlabel("Manual reviews per run")
    ax.set_ylabel("High-integrity records discarded per run")
    ax.grid(alpha=0.25,linestyle=":"); ax.set_axisbelow(True)
    ax.set_xlim(-0.9,11.4); ax.set_ylim(-0.85,3.95)
    save(fig, a.out, "fig2_protection_burden")

    # ---- Fig 3: does causal detection help as the entity mix shifts?
    S=defaultdict(lambda: defaultdict(list))
    for r in rows:
        S[r["policy"]][r["high_risk_update_share"]].append(float(r["high_risk_silent_overwrites"]))
    shares=sorted(S["last_write_wins"],key=float)
    fig,ax=plt.subplots(figsize=(7.2,4.4))
    style={"last_write_wins":("o","-","#2C3E50",4.2,9),
           "version_vector_causal":("s","--","#2E86C1",1.8,7),
           "crdt_field_merge":("^","-.","#28B463",1.8,7),
           "domain_aware":("*",":","#C0392B",2.0,13)}
    for p,(m,ls,c,lw,ms) in style.items():
        if p not in S: continue
        ax.plot([float(s)*100 for s in shares],[mean(S[p][s]) for s in shares],
                marker=m,linestyle=ls,color=c,label=LABEL[p],
                markersize=ms,linewidth=lw,
                markerfacecolor=c,markeredgecolor="white",markeredgewidth=0.7)
    ax.set_xlabel("High-integrity update share (%)")
    ax.set_ylabel("High-integrity records discarded per run")
    ax.set_xticks([float(s)*100 for s in shares])
    ax.legend(frameon=False,fontsize=9); ax.grid(alpha=0.25,linestyle=":"); ax.set_axisbelow(True)
    save(fig, a.out, "fig3_entity_mix")

    print("figures written to",a.out)
    for p in ORDER:
        if p in P: print(f"  {LABEL[p]:22} reviews={mean(P[p]['mr']):7.4f}  hi-integrity={mean(P[p]['hr']):7.4f}")
    print("\nFig3 data (high-integrity records discarded by entity mix):")
    for p in style:
        if p in S: print(f"  {LABEL[p]:22} " + "  ".join(f"{s}:{mean(S[p][s]):.3f}" for s in shares))

if __name__=="__main__": main()
