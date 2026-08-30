"""Rebuild the bazaar stage in app/bazaar_live.html to match the ChatDev look.

ChatDev ships no tileset (only character sprites), so the room is hand-built
here - which is legitimate: there is no source art to copy for the stage,
unlike the characters.

Three things changed:
  1. `#` was being painted with floorRunner(), so EVERY tile was teal carpet.
     The floor is now warm oak planks and the carpet is an explicit `r` tile.
  2. Palette warmed up (sandstone + oak + brass instead of cold slate blue).
  3. Floating pxText location labels replaced by wooden plaque signs, plus
     real props (plants, lamps, crates, barrels, shelves, chalkboard, monitor).

Idempotent: safe to re-run, it always patches from the same anchors.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = HERE.parent / "bazaar_live.html"

# ---------------------------------------------------------------- palette ---

OLD_PAL = '''  wood:"#b8875a", woodD:"#8d5f37", woodL:"#d7ae7d", woodGrain:"#6d4c31",
  wall:"#5b6577", wallD:"#39404e", wallTrim:"#7a8aa6",
  carpet:"#2f5d6b", carpetD:"#1f414b", carpetHi:"#4d8b9c",
  stone:"#93a2b8", stoneD:"#5b6a80", stoneHi:"#cad6e6",'''

NEW_PAL = '''  wood:"#c08a52", woodD:"#96612f", woodL:"#e0b47c", woodGrain:"#8a5c2c",
  woodSeam:"#5e3a17",
  /* pale lime plaster - deliberately much lighter than the oak floor so the
     room edges stay readable against it */
  wall:"#d5c9b2", wallD:"#a89a80", wallHi:"#efe7d6", wallTrim:"#7d5227",
  wallTrimD:"#54330f",
  rugRed:"#c04a48", rugCream:"#f4ecdb", rugBorder:"#8e2f33", rugFringe:"#eadfbf",
  stone:"#ded7c6", stoneD:"#a49b88", stoneHi:"#f5f1e7",
  flag:"#bcb4a1", flagD:"#8a8271", flagHi:"#dcd6c6",
  water:"#3aa7d8", waterD:"#1f6f9e", waterHi:"#a6e4fa",
  soil:"#4a2f1a", leaf:"#2f8f4a", leafD:"#1c5c30", leafHi:"#6fd06a",
  brass:"#c9a227", brassD:"#8a6c14",
  shade:"#f2dfab", shadeD:"#c9a86a", shadeHi:"#fff5d4", lampGlow:"#ffd97a",
  board:"#26383a", boardD:"#141f1e", chalk:"#e2f4ea",
  awningA:"#e0574c", awningB:"#f4efe2", awningD:"#a6332f",
  shelf:"#6d4c31", shelfD:"#432c19", screenD:"#060c12",'''

# ------------------------------------------------------------------ stage ---

NEW_STAGE = r'''/* Avalanche mix. The old `x*73+y*151` collapsed whenever the two arguments
   moved on a fixed lattice (e.g. hash2(9i, 4i+3) returned the same value for
   every i), which flattened the monitor's code lines and the shelf's books. */
function hash2(x,y){
  let h=(((x|0)*73856093)^((y|0)*19349663))|0;
  h=Math.imul(h^(h>>>13),1274126177);
  return (((h^(h>>>16))>>>0)%97+97)%97;
}

/* =====================================================================
   FLOORS - 16x16 tile painters, baked once into floorCv.
   ===================================================================== */

/* Warm oak boards. Two 8px bands per tile; alternate bands are staggered
   8px so the butt joints never line up into a grid. */
function floorWood(c,X,Y,tx,ty){
  const ch=hash2(tx,ty);
  c.fillStyle=P.wood; c.fillRect(X,Y,TILE,TILE);
  for(let b=0;b<2;b++){
    const band=ty*2+b, by=Y+b*8, sx=(band%2)*8;
    c.fillStyle=P.woodSeam; c.fillRect(X,by,TILE,1);      /* groove      */
    c.fillStyle=P.woodL;    c.fillRect(X,by+1,TILE,1);    /* lit bevel   */
    c.fillStyle=P.woodSeam; c.fillRect(X+sx,by+2,1,6);    /* butt joint  */
    if(ch%3===0){ c.fillStyle=P.woodGrain; c.fillRect(X+1+((ch+b*5)%10),by+3,4,1); }
    if(ch%5===1){ c.fillStyle=P.woodGrain; c.fillRect(X+7-((ch+b*3)%6),by+5,3,1); }
    if(ch%7===3){ c.fillStyle=P.woodD;     c.fillRect(X+((ch+b*2)%12),by+6,2,1); }
  }
}

/* Red-and-cream checkered rug. The fringe only appears on edges that are
   not shared with another rug tile, so any rectangle reads as one rug. */
function floorRug(c,X,Y,tx,ty){
  c.fillStyle=P.wood; c.fillRect(X,Y,TILE,TILE);
  const isR=function(a,b){ return mget(a,b)==="r"; };
  const N=isR(tx,ty-1),S=isR(tx,ty+1),E=isR(tx+1,ty),W=isR(tx-1,ty);
  c.fillStyle=P.rugBorder; c.fillRect(X,Y,TILE,TILE);
  c.fillStyle=P.rugFringe;
  if(!N) for(let i=0;i<8;i++) c.fillRect(X+i*2,Y,1,1);
  if(!S) for(let i=0;i<8;i++) c.fillRect(X+i*2,Y+15,1,1);
  if(!W) for(let i=0;i<8;i++) c.fillRect(X,Y+i*2,1,1);
  if(!E) for(let i=0;i<8;i++) c.fillRect(X+15,Y+i*2,1,1);
  /* Checkers stay aligned to world coordinates and are merely clipped to the
     inner area - otherwise the fringe inset makes the outermost row ragged
     and the border tiles stop lining up with the interior ones. */
  const x0=W?0:2, x1=E?0:2, y0=N?0:2, y1=S?0:2;
  for(let py=0;py<TILE;py+=4)for(let px=0;px<TILE;px+=4){
    const ax=Math.max(px,x0), bx2=Math.min(px+4,TILE-x1);
    const ay=Math.max(py,y0), by2=Math.min(py+4,TILE-y1);
    if(bx2<=ax||by2<=ay) continue;
    const odd=((((tx*TILE+px)>>2)+((ty*TILE+py)>>2))%2)===0;
    c.fillStyle=odd?P.rugRed:P.rugCream;
    c.fillRect(X+ax,Y+ay,bx2-ax,by2-ay);
  }
}

/* Flagstone marker - the queue spots in front of each station. */
function floorInlay(c,X,Y,tx,ty){
  floorWood(c,X,Y,tx,ty);
  c.fillStyle=P.flag;    c.fillRect(X+1,Y+1,14,14);
  c.fillStyle=P.flagHi;  c.fillRect(X+1,Y+1,14,1);
  c.fillStyle=P.flagD;   c.fillRect(X+1,Y+14,14,1); c.fillRect(X+14,Y+1,1,14);
  c.fillStyle=P.flagD;   c.fillRect(X+3,Y+5,10,1);  c.fillRect(X+3,Y+10,10,1);
  c.fillStyle=P.flagHi;  c.fillRect(X+3,Y+6,10,1);  c.fillRect(X+3,Y+11,10,1);
}

/* Courtyard pond - stone kerb drawn only where the water meets land. */
function floorPool(c,X,Y,tx,ty){
  const isP=function(a,b){ return mget(a,b)==="~"; };
  c.fillStyle=P.water;  c.fillRect(X,Y,TILE,TILE);
  c.fillStyle=P.waterD; c.fillRect(X,Y+TILE-5,TILE,5);   /* near edge in shade */
  c.fillStyle=P.water;  c.fillRect(X+1,Y+1,2,1); c.fillRect(X+11,Y+4,2,1);
  c.fillStyle=P.stone;
  if(!isP(tx,ty-1)) c.fillRect(X,Y,TILE,2);
  if(!isP(tx,ty+1)) c.fillRect(X,Y+14,TILE,2);
  if(!isP(tx-1,ty)) c.fillRect(X,Y,2,TILE);
  if(!isP(tx+1,ty)) c.fillRect(X+14,Y,2,TILE);
  c.fillStyle=P.stoneD;
  if(!isP(tx,ty-1)) c.fillRect(X,Y+2,TILE,1);
  if(!isP(tx+1,ty)) c.fillRect(X+13,Y+2,1,TILE);
}
function drawPoolShimmer(c,tx,ty,t){
  const X=tx*TILE,Y=ty*TILE;
  c.fillStyle=P.waterHi;
  const x=((t*2+tx*3+ty*5)%12)+2, y=((t*3+tx*7+ty*2)%10)+3;
  c.fillRect(X+x,Y+y,3,1);
  c.fillRect(X+((t+5)%12)+2,Y+((t+3)%9)+6,2,1);
}

/* Sandstone stall divider: lit cap, two staggered block courses, skirting. */
function wallPanel(c,X,Y,ty){
  c.fillStyle=P.wall;     c.fillRect(X,Y,TILE,TILE);
  c.fillStyle=P.wallHi;   c.fillRect(X,Y,TILE,2);
  c.fillStyle=P.wallTrim; c.fillRect(X,Y+2,TILE,1);
  c.fillStyle=P.wallD;    c.fillRect(X,Y+3,TILE,1);
  const o=(ty%2)?0:8;
  c.fillStyle=P.wallD;
  c.fillRect(X,Y+7,TILE,1);
  c.fillRect(X+((o+4)%16),Y+4,1,3);
  c.fillRect(X+o,Y+8,1,5);
  c.fillRect(X,Y+13,TILE,1);
  c.fillStyle=P.wallTrim;  c.fillRect(X,Y+14,TILE,1);
  c.fillStyle=P.wallTrimD; c.fillRect(X,Y+15,TILE,1);
}

function drawTile(c,ch,tx,ty,t){
  const X=tx*TILE,Y=ty*TILE;
  if(ch==="W") wallPanel(c,X,Y,ty);
  else if(ch==="~") floorPool(c,X,Y,tx,ty);
  else if(ch==="r") floorRug(c,X,Y,tx,ty);
  else if(ch==="D"||ch==="G"||ch==="S"||ch==="T"||ch==="R"||ch==="L")
    floorInlay(c,X,Y,tx,ty);
  else floorWood(c,X,Y,tx,ty);   /* "#" and anything unlisted = oak boards */
}

/* Static terrain baked once. Sized for the WHOLE map, not the viewport -
   a VW+TILE wide canvas clips everything east of x=16 and the render loop
   then blits empty space. Tile (tx,ty) lives at ((tx+BAKE_PAD)*TILE, ...),
   and render() must use the same pad when it reads back. */
const BAKE_PAD=2;
const floorCv=document.createElement("canvas");
floorCv.width=(MW+2*BAKE_PAD+1)*TILE; floorCv.height=(MH+2*BAKE_PAD+1)*TILE;
(function bake(){
  const fc=floorCv.getContext("2d");
  const t=0;
  for(let ty=-BAKE_PAD;ty<=MH+BAKE_PAD-1;ty++)for(let tx=-BAKE_PAD;tx<=MW+BAKE_PAD-1;tx++){
    const X=(tx+BAKE_PAD)*TILE,Y=(ty+BAKE_PAD)*TILE;
    const ch=(ty<0||ty>=MH||tx<0||tx>=MW)?"W":mget(tx,ty);
    drawTile(fc,ch,tx,ty,t);
  }
})();

/* =====================================================================
   PROPS - 16x16 world objects, depth-sorted with the actors in render().
   Tall props draw upward into negative Y (they occlude the tile behind).
   ===================================================================== */

function propBase(c,X,Y,w){
  c.fillStyle=P.woodSeam; c.fillRect(X+((16-w)>>1),Y+15,w,1);
}

function propPlant(c,X,Y){
  propBase(c,X,Y,10);
  c.fillStyle=P.potT;  c.fillRect(X+4,Y+9,8,6);
  c.fillStyle=P.potTD; c.fillRect(X+9,Y+9,3,6);
  c.fillStyle=P.potT;  c.fillRect(X+3,Y+7,10,2);
  c.fillStyle=P.potTD; c.fillRect(X+9,Y+7,4,2);
  c.fillStyle=P.soil;  c.fillRect(X+4,Y+7,8,1);
  c.fillStyle=P.leafD;
  c.fillRect(X+3,Y+3,3,5); c.fillRect(X+10,Y+4,3,4); c.fillRect(X+6,Y+5,4,2);
  c.fillStyle=P.leaf;
  c.fillRect(X+5,Y+1,2,7); c.fillRect(X+9,Y+2,2,6); c.fillRect(X+7,Y+0,3,7);
  c.fillStyle=P.leafHi;
  c.fillRect(X+7,Y-1,2,2); c.fillRect(X+4,Y+3,1,2); c.fillRect(X+11,Y+4,1,2);
}

function propLamp(c,X,Y,t){
  propBase(c,X,Y,8);
  c.fillStyle=P.brass;  c.fillRect(X+5,Y+12,6,3);
  c.fillStyle=P.brassD; c.fillRect(X+9,Y+12,2,3);
  c.fillStyle=P.brass;  c.fillRect(X+7,Y+3,2,10);
  c.fillStyle=P.brassD; c.fillRect(X+8,Y+3,1,10);
  c.fillStyle=P.shade;  c.fillRect(X+3,Y-6,10,9);
  c.fillStyle=P.shadeD; c.fillRect(X+10,Y-6,3,9);
  c.fillStyle=P.shadeHi;c.fillRect(X+4,Y-6,8,1);
  const f=0.10+0.06*Math.sin(t/22);
  c.fillStyle=P.lampGlow;
  c.globalAlpha=f*0.40; c.fillRect(X-6,Y+9,28,7);
  c.globalAlpha=f*0.75; c.fillRect(X-2,Y+11,20,5);
  c.globalAlpha=f;      c.fillRect(X+2,Y+13,12,3);
  c.globalAlpha=1;
}

function propCrate(c,X,Y){
  propBase(c,X,Y,14);
  c.fillStyle=P.wood;   c.fillRect(X+1,Y+2,14,13);
  c.fillStyle=P.woodD;  c.fillRect(X+11,Y+2,4,13);
  c.fillStyle=P.woodL;  c.fillRect(X+1,Y+2,14,1);
  c.fillStyle=P.woodSeam;
  c.fillRect(X+1,Y+2,1,13); c.fillRect(X+14,Y+2,1,13);
  c.fillRect(X+1,Y+14,14,1);
  c.fillStyle=P.woodGrain;
  for(let i=0;i<6;i++) c.fillRect(X+2,Y+3+i*2,12,1);
  c.fillStyle=P.woodSeam;
  for(let i=0;i<11;i++){ c.fillRect(X+2+i,Y+3+i,1,1); c.fillRect(X+13-i,Y+3+i,1,1); }
}

function propBarrel(c,X,Y){
  propBase(c,X,Y,12);
  c.fillStyle=P.wood;  c.fillRect(X+2,Y+3,12,12);
  c.fillStyle=P.woodD; c.fillRect(X+10,Y+3,4,12);
  c.fillStyle=P.woodL; c.fillRect(X+4,Y+3,3,12);
  c.fillStyle=P.brass; c.fillRect(X+2,Y+5,12,1); c.fillRect(X+2,Y+11,12,1);
  c.fillStyle=P.brassD;c.fillRect(X+9,Y+5,5,1);  c.fillRect(X+9,Y+11,5,1);
  c.fillStyle=P.woodSeam; c.fillRect(X+2,Y+3,12,1); c.fillRect(X+2,Y+14,12,1);
  c.fillStyle=P.wood;  c.fillRect(X+3,Y+2,10,1);
  c.fillStyle=P.woodD; c.fillRect(X+9,Y+2,4,1);
}

function propShelf(c,X,Y){
  propBase(c,X,Y,16);
  c.fillStyle=P.shelfD; c.fillRect(X,Y-9,16,25);
  c.fillStyle=P.wood;   c.fillRect(X+1,Y-8,14,23);
  c.fillStyle=P.woodD;  c.fillRect(X+11,Y-8,4,23);
  /* Four boards 7 rows apart; each compartment's books stand on the board
     below it and hang from the one above, so nothing overlaps. */
  const boards=[Y-8,Y-1,Y+6,Y+13], cols=[P.red,P.sky,P.gold,P.green,P.plum,P.teal];
  for(let k=0;k<boards.length;k++){
    const ry=boards[k];
    if(k>0){
      let bx=X+2;
      for(let i=0;i<6;i++){
        const w=1+(hash2(k*13+i*7+1,k*5+i)%3);
        if(bx+w>X+14) break;
        c.fillStyle=cols[hash2(i*5+k*3+2,i*11+3)%6];
        c.fillRect(bx,ry-5,w,5);
        c.fillStyle=P.woodSeam; c.fillRect(bx,ry-1,w,1);
        bx+=w+1;
      }
    }
    c.fillStyle=P.wood;     c.fillRect(X+1,ry,14,2);
    c.fillStyle=P.woodSeam; c.fillRect(X+1,ry+2,14,1);
  }
}

function propChalkboard(c,X,Y){
  propBase(c,X,Y,14);
  c.fillStyle=P.wood;   c.fillRect(X+2,Y+8,2,7); c.fillRect(X+12,Y+8,2,7);
  c.fillStyle=P.woodD;  c.fillRect(X+3,Y+8,1,7); c.fillRect(X+13,Y+8,1,7);
  c.fillStyle=P.wood;     c.fillRect(X+1,Y-6,14,15);
  c.fillStyle=P.woodD;    c.fillRect(X+12,Y-6,3,15);
  c.fillStyle=P.woodSeam; c.fillRect(X+1,Y-6,14,1); c.fillRect(X+1,Y+8,14,1);
  c.fillStyle=P.board;  c.fillRect(X+2,Y-5,12,12);
  c.fillStyle=P.boardD; c.fillRect(X+2,Y-5,12,1); c.fillRect(X+2,Y-5,1,12);
  c.fillStyle=P.chalk;
  c.fillRect(X+4,Y-3,7,1); c.fillRect(X+4,Y-1,5,1);
  c.fillRect(X+4,Y+1,8,1); c.fillRect(X+4,Y+3,4,1);
  c.fillRect(X+4,Y+5,6,1);
}

function propMonitor(c,X,Y,t){
  propBase(c,X,Y,12);
  c.fillStyle=P.metal;  c.fillRect(X+6,Y+11,4,4);
  c.fillStyle=P.metalD; c.fillRect(X+8,Y+11,2,4);
  c.fillStyle=P.metal;  c.fillRect(X+3,Y+14,10,1);
  c.fillStyle=P.metalD; c.fillRect(X+1,Y+1,14,12);
  c.fillStyle=P.metal;  c.fillRect(X+1,Y+1,13,11);
  c.fillStyle=P.screen; c.fillRect(X+2,Y+2,11,9);
  c.fillStyle=P.screenGlow;
  for(let i=0;i<4;i++){
    const w=3+(hash2(i*9+((t/14)|0),i*4+3)%6);
    c.fillRect(X+3,Y+3+i*2,w,1);
  }
  c.fillStyle=P.green; c.fillRect(X+2,Y+10,11,1);
}

/* Wooden plaque on two posts - replaces every floating pxText label. */
function propSign(c,X,Y,label,col){
  const w=String(label).length, bw=w*4+6, bx=X+8-(bw>>1);
  c.fillStyle=P.woodSeam; c.fillRect(X+2,Y+14,3,2); c.fillRect(X+11,Y+14,3,2);
  c.fillStyle=P.wood;  c.fillRect(X+3,Y+6,2,9); c.fillRect(X+11,Y+6,2,9);
  c.fillStyle=P.woodD; c.fillRect(X+4,Y+6,1,9); c.fillRect(X+12,Y+6,1,9);
  /* board: 14 rows tall, text sits 4 rows down so the plaque is not top-heavy */
  c.fillStyle=P.woodSeam; c.fillRect(bx,Y-8,bw,14);
  c.fillStyle=P.wood;     c.fillRect(bx+1,Y-7,bw-2,12);
  c.fillStyle=P.woodL;    c.fillRect(bx+1,Y-7,bw-2,1);
  c.fillStyle=P.woodD;    c.fillRect(bx+1,Y+3,bw-2,2);
  c.fillStyle=P.brass;
  c.fillRect(bx+2,Y-6,1,1); c.fillRect(bx+bw-3,Y-6,1,1);
  c.fillRect(bx+2,Y+1,1,1); c.fillRect(bx+bw-3,Y+1,1,1);
  pxText(c,label,X+8-((w*4-1)>>1),Y-4,col||P.ink,1);
}

function drawProp(c,pr,t){
  const X=pr.x*TILE, Y=pr.y*TILE;
  if(pr.t==="plant")   propPlant(c,X,Y);
  else if(pr.t==="lamp")    propLamp(c,X,Y,t);
  else if(pr.t==="crate")   propCrate(c,X,Y);
  else if(pr.t==="barrel")  propBarrel(c,X,Y);
  else if(pr.t==="shelf")   propShelf(c,X,Y);
  else if(pr.t==="board")   propChalkboard(c,X,Y);
  else if(pr.t==="monitor") propMonitor(c,X,Y,t);
  else if(pr.t==="sign")    propSign(c,X,Y,pr.label,pr.col);
}

/* Tile placement. Nothing here is solid - walking is scripted in live mode -
   so props are pure decoration and never block a path. */
const PROPS=[
  /* stall side */
  {t:"sign",   x:5,  y:2,  label:"CATALOG"},
  {t:"plant",  x:2,  y:3},
  {t:"shelf",  x:1,  y:5},
  {t:"crate",  x:8,  y:4},
  {t:"barrel", x:8,  y:6},
  {t:"plant",  x:8,  y:8},
  /* west corridor */
  {t:"plant",  x:11, y:3},
  {t:"crate",  x:10, y:5},
  {t:"lamp",   x:10, y:9},
  /* lower west */
  {t:"crate",  x:4,  y:9},
  {t:"barrel", x:7,  y:9},
  {t:"sign",   x:6,  y:12, label:"AUDIT"},
  {t:"plant",  x:7,  y:14},
  {t:"barrel", x:6,  y:17},
  {t:"crate",  x:1,  y:16},
  /* plaza */
  {t:"plant",  x:13, y:3},
  {t:"lamp",   x:16, y:4},
  {t:"board",  x:24, y:3},
  {t:"crate",  x:26, y:4},
  {t:"barrel", x:27, y:5},
  {t:"sign",   x:22, y:3,  label:"MANDATE"},
  {t:"sign",   x:17, y:4,  label:"RISK GATE"},
  {t:"lamp",   x:24, y:8},
  {t:"plant",  x:22, y:10},
  {t:"monitor",x:25, y:9},
  {t:"shelf",  x:28, y:10},
  {t:"lamp",   x:13, y:11},
  {t:"crate",  x:26, y:13},
  {t:"barrel", x:18, y:14},
  {t:"plant",  x:20, y:17},
  {t:"lamp",   x:23, y:17},
  {t:"crate",  x:27, y:17},
];

/* =====================================================================
   STATIONS
   ===================================================================== */

function drawStallBack(c,t){
  const x=stall.x*TILE, y=stall.y*TILE, w=stall.w*TILE, h=stall.h*TILE;
  /* striped canopy */
  for(let i=0;i<w/8;i++){
    c.fillStyle=(i%2)?P.awningA:P.awningB;
    c.fillRect(x+i*8,y-8,8,6);
  }
  c.fillStyle=P.awningD; c.fillRect(x,y-3,w,2);
  /* posts */
  c.fillStyle=P.wood;  c.fillRect(x,y-1,3,h+1);     c.fillRect(x+w-3,y-1,3,h+1);
  c.fillStyle=P.woodD; c.fillRect(x+2,y-1,1,h+1);   c.fillRect(x+w-1,y-1,1,h+1);
  /* carcass */
  c.fillStyle=P.wood;     c.fillRect(x,y,w,h);
  c.fillStyle=P.woodD;    c.fillRect(x+w-6,y,6,h);
  c.fillStyle=P.woodSeam; c.fillRect(x,y,w,1); c.fillRect(x,y+h-1,w,1);
  c.fillStyle=P.woodGrain;
  for(let py=y+4;py<y+h-2;py+=5) c.fillRect(x+1,py,w-7,1);
  /* back shelf with jars of goods */
  c.fillStyle=P.shelfD; c.fillRect(x+4,y+4,w-8,14);
  c.fillStyle=P.shelf;  c.fillRect(x+4,y+4,w-8,12);
  c.fillStyle=P.wood;   c.fillRect(x+4,y+16,w-8,2);
  c.fillStyle=P.woodL;  c.fillRect(x+4,y+16,w-8,1);
  const jar=[P.red,P.gold,P.green,P.teal,P.plum,P.sky];
  for(let i=0;i<6;i++){
    const jx=x+6+i*9;
    if(jx+6>x+w-6) break;
    c.fillStyle=jar[i];  c.fillRect(jx,y+8,5,7);
    c.fillStyle=P.white; c.fillRect(jx+1,y+8,2,1);
    c.fillStyle=P.ink;   c.fillRect(jx,y+15,5,1);
    c.fillStyle=P.brass; c.fillRect(jx+1,y+6,3,2);
  }
  /* marble slab */
  c.fillStyle=P.stone;   c.fillRect(x-3,y+22,w+6,6);
  c.fillStyle=P.stoneHi; c.fillRect(x-3,y+22,w+6,1);
  c.fillStyle=P.stoneD;  c.fillRect(x-3,y+27,w+6,1);
  c.fillStyle=P.stoneHi; c.fillRect(x+6,y+24,10,1); c.fillRect(x+30,y+25,8,1);
  /* ledger and brass bell on the slab */
  c.fillStyle=P.red;    c.fillRect(x+8,y+19,9,3);
  c.fillStyle=P.white;  c.fillRect(x+9,y+19,7,1);
  c.fillStyle=P.ink;    c.fillRect(x+8,y+21,9,1);
  c.fillStyle=P.brass;  c.fillRect(x+w-22,y+18,5,4);
  c.fillStyle=P.brassD; c.fillRect(x+w-18,y+18,1,4);
  c.fillStyle=P.brass;  c.fillRect(x+w-23,y+21,7,1);
}
function drawStallFront(c){
  const x=stall.x*TILE, y=(stall.y+stall.h)*TILE-3;
  c.fillStyle=P.woodSeam; c.fillRect(x,y,stall.w*TILE,3);
  c.fillStyle=P.woodD;    c.fillRect(x,y,stall.w*TILE,2);
}

function drawTower(c,t){
  const x=towerP.x*TILE, y=towerP.y*TILE;
  c.fillStyle=P.metalD; c.fillRect(x+3,y+2,26,42);
  c.fillStyle=P.metal;  c.fillRect(x+4,y+3,24,40);
  c.fillStyle=P.metalD; c.fillRect(x+21,y+3,7,40);
  for(let i=0;i<5;i++){
    const by=y+5+i*7;
    c.fillStyle=P.screen;  c.fillRect(x+6,by,14,5);
    c.fillStyle=P.screenD; c.fillRect(x+6,by,14,1);
    const lit=(((t/12)|0)+i)%3;
    c.fillStyle=lit===0?P.green:(lit===1?P.gold:P.red);
    c.fillRect(x+9,by+2,2,2);
    c.fillStyle=P.screenGlow; c.fillRect(x+12,by+2,1,1);
    c.fillStyle=P.metal;      c.fillRect(x+22,by+1,3,3);
  }
  c.fillStyle=P.stone;   c.fillRect(x+1,y+44,30,4);
  c.fillStyle=P.stoneHi; c.fillRect(x+1,y+44,30,1);
  c.fillStyle=P.stoneD;  c.fillRect(x+1,y+47,30,1);
}

function drawShrine(c,t){
  const x=shrine.x*TILE, y=shrine.y*TILE, w=shrine.w*TILE, h=shrine.h*TILE;
  c.fillStyle=P.stoneD; c.fillRect(x,y+6,w,h-4);
  c.fillStyle=P.stone;  c.fillRect(x+1,y+6,w-2,h-5);
  c.fillStyle=P.stoneHi;c.fillRect(x+1,y+6,w-2,1);
  /* lectern */
  c.fillStyle=P.wood;   c.fillRect(x-2,y+2,w+4,5);
  c.fillStyle=P.woodL;  c.fillRect(x-2,y+2,w+4,1);
  c.fillStyle=P.woodD;  c.fillRect(x-2,y+6,w+4,1);
  /* the open ledger */
  c.fillStyle=P.white;  c.fillRect(x+3,y-6,w-6,8);
  c.fillStyle=P.stoneD; c.fillRect(x+3,y+1,w-6,1);
  c.fillStyle=P.ink;    c.fillRect(x+(w>>1)-1,y-6,1,8);
  c.fillStyle=P.stone;
  for(let i=0;i<3;i++){
    c.fillRect(x+5,y-4+i*2,6,1); c.fillRect(x+(w>>1)+2,y-4+i*2,6,1);
  }
  /* quill */
  c.fillStyle=P.brass; c.fillRect(x+w-6,y-12,1,8);
  c.fillStyle=P.white; c.fillRect(x+w-7,y-13,3,2);
  /* seal glow */
  c.globalAlpha=0.30+0.22*Math.sin(t/16);
  c.fillStyle=P.gold; c.fillRect(x+(w>>1)-4,y-10,8,3);
  c.globalAlpha=1;
}

function drawGate(c,t){
  const x=gate.x*TILE, y=gate.y*TILE, on=gateOpen>0;
  c.fillStyle=P.metalD; c.fillRect(x-6,y-10,4,40); c.fillRect(x+18,y-10,4,40);
  c.fillStyle=P.metal;  c.fillRect(x-6,y-10,3,40); c.fillRect(x+18,y-10,3,40);
  c.fillStyle=P.metalD; c.fillRect(x-8,y-14,32,6);
  c.fillStyle=P.metal;  c.fillRect(x-8,y-14,31,4);
  c.fillStyle=P.wood;   c.fillRect(x-7,y-10,30,3);
  c.fillStyle=P.woodL;  c.fillRect(x-7,y-10,30,1);
  c.fillStyle=P.metalD; c.fillRect(x+8,y-12,10,5);
  c.fillStyle=on?P.green:(paidFlag?P.gold:P.red);
  c.fillRect(x+9,y-11,8,3);
  c.fillStyle=P.white;  c.fillRect(x+10,y-11,2,1);
  c.fillStyle=P.metalD;
  if(on){ c.fillRect(x+2,y+12,14,2); }
  else  { c.fillRect(x+2,y+6,2,16); c.fillRect(x+2,y+20,14,2); }
}
'''

# ---------------------------------------------------------------- patches ---


def patch(src: str, name: str, old: str, new: str) -> str:
    n = src.count(old)
    if n != 1:
        sys.exit(f"[{name}] expected exactly 1 anchor, found {n}")
    return src.replace(old, new)


def main() -> None:
    src = HTML.read_text(encoding="utf-8")
    before = len(src)

    src = patch(src, "palette", OLD_PAL, NEW_PAL)

    # the whole stage block: hash2() .. drawGate()
    pat = re.compile(
        r"function hash2\(x,y\)\{.*?\n\}\n\n/\* expose top-level consts to window",
        re.S,
    )
    if len(pat.findall(src)) != 1:
        sys.exit("[stage] expected exactly 1 stage block")
    src = pat.sub(NEW_STAGE + "\n/* expose top-level consts to window", src)

    # rugs: two carpeted areas, in front of the counter and the plaza
    src = patch(
        src,
        "rugs",
        '  mset(19,4,"T"); mset(14,6,"R"); mset(3,13,"L");\n}',
        '  mset(19,4,"T"); mset(14,6,"R"); mset(3,13,"L");\n'
        '  /* carpets: in front of the counter, and the plaza meeting area */\n'
        '  function rug(x0,y0,x1,y1){'
        'for(let yy=y0;yy<=y1;yy++)for(let xx=x0;xx<=x1;xx++)mset(xx,yy,"r");}\n'
        '  rug(4,7,8,8); rug(15,8,20,11);\n}',
    )

    # the prof was parked inside a wall column (x=9 is "W" for y=2..19)
    src = patch(
        src,
        "prof-pos",
        'prof:{name:"PROF.EXPERIMENT",px:9*TILE,py:7*TILE,',
        'prof:{name:"PROF.EXPERIMENT",px:17*TILE,py:8*TILE,',
    )

    # depth-sort the props with the actors
    src = patch(
        src,
        "props-in-render",
        "  items.sort((a,b)=>a.y-b.y).forEach(it=>it.z());",
        "  for(let pi=0;pi<PROPS.length;pi++){ const pr=PROPS[pi];\n"
        "    items.push({y:pr.y*TILE+TILE, z:function(){ drawProp(ctx,pr,frame); }});\n"
        "  }\n"
        "  items.sort((a,b)=>a.y-b.y).forEach(it=>it.z());",
    )

    HTML.write_text(src, encoding="utf-8")
    print(f"patched bazaar_live.html  {before} -> {len(src)} bytes")

    checks = [
        ("floorWood planks", "function floorWood(c,X,Y,tx,ty){"),
        ("floorRug", "function floorRug(c,X,Y,tx,ty){"),
        ("floorPool kerb", "if(!isP(tx,ty-1)) c.fillRect(X,Y,TILE,2);"),
        ("propSign", "function propSign(c,X,Y,label,col){"),
        ("drawProp", "function drawProp(c,pr,t){"),
        ("PROPS table", "const PROPS=["),
        ("props in render", "items.push({y:pr.y*TILE+TILE"),
        ("rug tiles", 'rug(4,7,8,8); rug(15,8,20,11);'),
        ("woodSeam", 'woodSeam:"#5e3a17"'),
        ("prof moved", 'px:17*TILE,py:8*TILE'),
    ]
    bad = [n for n, s in checks if s not in src]
    if bad:
        sys.exit("post-check FAILED: " + ", ".join(bad))
    print("post-check: all new symbols present")
    for gone in ("carpet:", "floorRunner"):
        if gone in src:
            sys.exit(f"post-check FAILED: stale '{gone}' still present")
    print("post-check: old carpet stage fully removed")


if __name__ == "__main__":
    main()
