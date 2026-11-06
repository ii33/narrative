import QtQuick
import QtQuick.Controls
Rectangle{
 id:view;property var desk;color:"#e4e1d7";clip:true
 Canvas{id:canvas;anchors.fill:parent;onPaint:{var c=getContext("2d");c.reset();c.fillStyle="#dce3e4";c.fillRect(0,0,width,height/2);c.fillStyle="#b9b5a6";c.fillRect(0,height/2,width,height/2);c.strokeStyle="#8f928b";for(var i=0;i<8;i++){c.beginPath();c.moveTo(width/2,height/2);c.lineTo(i*width/7,height);c.stroke()}var rows=(view.desk.cameraView.projections||[]).filter(p=>p.visible).slice().sort((a,b)=>b.depth-a.depth);rows.forEach(function(p){var o=view.desk.stageObjects.find(o=>o.id===p.object_id)||{},h=Math.max(2,p.size_ratio*height),w=h*(o.kind==="person"?0.3:0.65),x=p.screen_x*width,y=p.screen_y*height;c.globalAlpha=p.occluded?0.35:1;c.fillStyle=o.kind==="person"?"#617d72":o.kind==="prop"?"#ae875c":"#99968b";c.strokeStyle=p.in_focus?"#c48728":"#3f504a";c.lineWidth=p.in_focus?3:1;c.fillRect(x-w/2,y-h/2,w,h);c.strokeRect(x-w/2,y-h/2,w,h);c.globalAlpha=1;c.fillStyle="#203c30";c.font="11px sans-serif";c.fillText(o.name||"",x-w/2,y-h/2-3)})}}
 Connections{target:desk;function onCameraViewChanged(){canvas.requestPaint()}}
 onWidthChanged:canvas.requestPaint()
 onHeightChanged:canvas.requestPaint()
 Label{anchors.left:parent.left;anchors.top:parent.top;text:"机位视图 · 2.5D近似（对焦高亮非光学景深）";padding:5;background:Rectangle{color:"#dbe4da"}}
}
