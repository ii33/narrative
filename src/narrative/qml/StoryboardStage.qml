import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
 id:stage
 property var desk
 color:"#e6ebe6";border.color:desk.t.line;clip:true
 ColumnLayout{anchors.fill:parent;anchors.margins:8;spacing:4
  RowLayout{Layout.fillWidth:true;Label{text:"二维调度板";font.bold:true}Label{text:"动作 "+desk.seconds(desk.actionTime);Layout.fillWidth:true}ToolButton{objectName:"storyboardStageMaximize";text:desk.enlargedView==="stage"?"还原":"放大";onClicked:desk.enlargedView=desk.enlargedView==="stage"?"":"stage"}}
  RowLayout{Layout.fillWidth:true;ComboBox{objectName:"storyboardStageObjectChoice";Layout.fillWidth:true;model:desk.board.objects||[];textRole:"name";currentIndex:model.findIndex(o=>o.id===desk.selectedObjectId);displayText:desk.selectedObjectId?currentText:"选择场面对象";onActivated:desk.selectedObjectId=model[currentIndex].id}Button{objectName:"storyboardNewObject";text:"添加对象";enabled:desk.ready&&!desk.busy&&!desk.approved;onClicked:desk.openObjectEditor()}ToolButton{objectName:"storyboardObjectMenu";text:"操作";enabled:!!desk.selectedObjectId&&!desk.busy&&!desk.approved;onClicked:objectMenu.open();Menu{id:objectMenu;MenuItem{objectName:"storyboardEditObject";text:"属性";onTriggered:desk.openObjectEditor(false)}MenuItem{objectName:"storyboardObjectKeys";text:"动作关键点";onTriggered:desk.openActionKeys()}MenuItem{objectName:"storyboardDeleteObject";text:"删除对象";onTriggered:desk.save({op:"delete_object",object_id:desk.selectedObjectId})}}}}
  RowLayout{Layout.fillWidth:true;CheckBox{objectName:"storyboardRecord";text:"记录关键点";checked:desk.recording;enabled:!!desk.selectedObjectId&&!desk.approved&&!desk.busy;onToggled:desk.recording=checked}ComboBox{objectName:"storyboardInterpolation";model:["保持","线性"];currentIndex:desk.interpolation==="linear"?1:0;onActivated:desk.interpolation=currentIndex?"linear":"hold"}}
  RowLayout{Layout.fillWidth:true;Label{text:"朝向"}SpinBox{id:heading;objectName:"storyboardObjectHeading";from:-36000;to:36000;value:Math.round(desk.selectedObject.rotation||0);editable:true;enabled:desk.recording&&!desk.busy}Button{objectName:"storyboardRecordHeading";text:"记录朝向";enabled:desk.recording&&!desk.busy;onClicked:desk.writeObjectPoint(desk.selectedObjectId,desk.actionTime,desk.selectedObject.world_x,desk.selectedObject.world_y,desk.board.revision,heading.value)}}
  Label{objectName:"storyboardRecordingTarget";text:desk.recording?"记录："+(desk.selectedObject.name||"")+" · 动作 "+desk.seconds(desk.actionTime):"未开启记录：拖动不会修改站位。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.recording?"#9a4429":desk.t.muted}
  Item{id:field;objectName:"storyboardStageField";
   readonly property bool hasCamera:((desk.shot.camera||{}).keys||[]).length>0
   readonly property var camera:desk.cameraView.position||{x:0.5,y:-1}
   readonly property real minX:hasCamera?Math.min(-0.15,camera.x-0.15):0
   readonly property real maxX:hasCamera?Math.max(1.15,camera.x+0.15):1
   readonly property real minY:hasCamera?Math.min(-0.15,camera.y-0.15):0
   readonly property real maxY:hasCamera?Math.max(1.15,camera.y+0.15):1
   function wx(value,w){return(value-minX)/(maxX-minX)*(width-w)}
   function wy(value,h){return(value-minY)/(maxY-minY)*(height-h)}Layout.fillWidth:true;Layout.fillHeight:true;clip:true
   Canvas{id:stageCanvas;anchors.fill:parent;onPaint:{var c=getContext("2d");c.reset();c.strokeStyle="#cad2ca";c.lineWidth=1;for(var i=0;i<=10;i++){var x=field.wx(i/10,38)+19,y=field.wy(i/10,38)+19;c.beginPath();c.moveTo(x,0);c.lineTo(x,height);c.stroke();c.beginPath();c.moveTo(0,y);c.lineTo(width,y);c.stroke()}if(field.hasCamera){var p=field.camera,a=desk.cameraView.yaw*Math.PI/180,half=Math.atan(Math.tan(desk.cameraView.fov_deg*Math.PI/360)*16/9);function px(x){return field.wx(x,38)+19}function py(y){return field.wy(y,38)+19}c.fillStyle="rgba(57,115,172,0.16)";c.strokeStyle="#3973ac";c.beginPath();c.moveTo(px(p.x),py(p.y));c.lineTo(px(p.x+Math.sin(a-half)),py(p.y+Math.cos(a-half)));c.lineTo(px(p.x+Math.sin(a+half)),py(p.y+Math.cos(a+half)));c.closePath();c.fill();c.stroke();c.fillStyle="#3973ac";c.fillRect(px(p.x)-6,py(p.y)-5,12,10)}}}
   Connections{target:desk;function onCameraViewChanged(){stageCanvas.requestPaint()}}
   onWidthChanged:stageCanvas.requestPaint()
   onHeightChanged:stageCanvas.requestPaint()
   Repeater{model:desk.stageObjects
    Item{id:actor;required property var modelData;property string objectId:modelData.id;objectName:"storyboardStageObject";z:modelData.world_depth||0;width:38;height:38;x:field.wx(modelData.world_x,width);y:field.wy(modelData.world_y,height)
     function restorePose(){actor.x=Qt.binding(function(){return field.wx(actor.modelData.world_x,actor.width)});actor.y=Qt.binding(function(){return field.wy(actor.modelData.world_y,actor.height)})}
     Rectangle{anchors.fill:parent;radius:actor.modelData.kind==="person"?19:4;color:desk.selectedObjectId===actor.objectId?"#e8b77a":"#aac3b8";border.width:2;border.color:"#385849"}
     Rectangle{width:3;height:16;color:"#385849";anchors.centerIn:parent;rotation:actor.modelData.world_rotation;transformOrigin:Item.Bottom}
     Label{text:actor.modelData.name;anchors.top:parent.bottom;anchors.horizontalCenter:parent.horizontalCenter;font.pixelSize:10;color:"#253e32"}
     MouseArea{anchors.fill:parent;cursorShape:desk.recording?Qt.SizeAllCursor:Qt.PointingHandCursor;property real px;property real py;property double at;property string target;property int rev;property string owner;property string project
      onPressed:function(mouse){desk.playing=false;desk.selectedObjectId=actor.objectId;px=mouse.x;py=mouse.y;at=desk.actionTime;target=actor.objectId;rev=desk.board.revision;owner=desk.unitId;project=desk.app.project.path}
      onPositionChanged:function(mouse){if(pressed&&desk.recording){var p=mapToItem(field,mouse.x-px,mouse.y-py);actor.x=Math.max(0,Math.min(field.width-actor.width,p.x));actor.y=Math.max(0,Math.min(field.height-actor.height,p.y))}}
      onReleased:{var x=field.minX+actor.x/Math.max(1,field.width-actor.width)*(field.maxX-field.minX),y=field.minY+actor.y/Math.max(1,field.height-actor.height)*(field.maxY-field.minY);actor.restorePose();if(desk.recording&&!desk.busy&&!desk.approved&&owner===desk.unitId&&project===desk.app.project.path)desk.writeObjectPoint(target,at,x,y,rev)}
      onCanceled:actor.restorePose()
     }
    }
   }
  }
 }
}
