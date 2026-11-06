import QtQuick
import "StoryboardMotion.js" as Motion

Item{
 id:art
 property var desk
 property alias source:base.source
 property alias status:base.status
 property alias fillMode:base.fillMode
 property alias cache:base.cache
 clip:true
 readonly property var animation:desk.shot.animation||({})
 readonly property var initial:desk.shot.id?Motion.cameraView(desk.board,desk.shot,0,Motion.objects(desk.board.objects||[],desk.shot.action_start_ms||0)):({})
 readonly property real zoom:animation.mode==="camera"?Math.max(0.05,Math.tan((initial.fov_deg||50)*Math.PI/360)/Math.tan((desk.cameraView.fov_deg||50)*Math.PI/360)):1
 readonly property real horizontalFov:2*Math.atan(Math.tan((desk.cameraView.fov_deg||50)*Math.PI/360)*16/9)*180/Math.PI
 readonly property real dx:animation.mode==="camera"?((desk.cameraView.position?.x||0)-(initial.position?.x||0))*960+((desk.cameraView.yaw||0)-(initial.yaw||0))*960/horizontalFov:0
 readonly property real dy:animation.mode==="camera"?((desk.cameraView.position?.z||0)-(initial.position?.z||0))*540+((desk.cameraView.pitch||0)-(initial.pitch||0))*540/(desk.cameraView.fov_deg||50):0
 readonly property real imageX:480*(1-zoom)-zoom*dx
 readonly property real imageY:270*(1-zoom)+zoom*dy
 Item{
  id:canvas;width:960;height:540;scale:Math.min(art.width/960,art.height/540);transformOrigin:Item.TopLeft;x:(art.width-width*scale)/2;y:(art.height-height*scale)/2;clip:true
  Rectangle{anchors.fill:parent;color:"black"}
  Image{id:base;x:art.imageX;y:art.imageY;width:960*art.zoom;height:540*art.zoom;fillMode:Image.PreserveAspectFit;cache:false}
  Repeater{model:art.animation.mode==="layers"?art.animation.layers||[]:[]
   Image{
    objectName:"storyboardAnimatedLayer"
    required property var modelData
    property string layerId:modelData.id
    readonly property var projection:(desk.cameraView.projections||[]).find(p=>p.object_id===modelData.object_id)
    readonly property var initialProjection:(art.initial.projections||[]).find(p=>p.object_id===modelData.object_id)
    readonly property real perspectiveScale:projection&&initialProjection&&initialProjection.size_ratio?projection.size_ratio/initialProjection.size_ratio:1
    visible:desk.localTime>=modelData.start_ms&&desk.localTime<modelData.end_ms&&(!projection||projection.visible)
    source:bridge&&modelData.path?bridge.storyboardImageUrl(modelData.path):""
    width:Math.max(1,Math.round(implicitWidth*(modelData.scale||1)*perspectiveScale))
    height:Math.max(1,Math.round(implicitHeight*(modelData.scale||1)*perspectiveScale))
    opacity:modelData.opacity===undefined?1:modelData.opacity
    x:Math.round((projection?projection.screen_x:(modelData.x===undefined?0.5:modelData.x))*960-width/2)
    y:Math.round((projection?projection.screen_y:(modelData.y===undefined?0.5:modelData.y))*540-height/2)
    fillMode:Image.PreserveAspectFit
   }
  }
 }
}
