import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
Dialog{
 id:editor;objectName:"storyboardAnimationDialog";property var desk;property var layers:[];property int selected:-1;property int revision:0;property string shotId:""
    SubmissionState{id:contentSubmission;owner:editor;operation:"set_storyboard_animation";target:({unit_id:editor.desk.unitId||null,shot_id:editor.shotId});onRestoreRequested:function(a){editor.layers=a.layers||[];editor.revision=a.revision;editor.shotId=a.shot_id;mode.currentIndex=mode.values.indexOf(a.mode)}}

 title:"局部草图动画";modal:true;anchors.centerIn:parent;width:Math.min(650,desk.width-24);height:Math.min(620,desk.height-24)
 function openFor(){layers=JSON.parse(JSON.stringify((desk.shot.animation||{}).layers||[]));selected=-1;shotId=desk.shot.id;revision=desk.board.revision;mode.currentIndex=mode.values.indexOf((desk.shot.animation||{}).mode||"hold");clearLayer();open()}
 function clearLayer(){path.text="";start.value=0;end.value=desk.shot.duration_ms;layerScale.text="1";layerOpacity.text="1";layerX.text="0.5";layerY.text="0.5";objectChoice.currentIndex=0}
 function loadLayer(i){selected=i;var row=layers[i];path.text=row.path;start.value=row.start_ms;end.value=row.end_ms;layerScale.text=String(row.scale||1);layerOpacity.text=String(row.opacity===undefined?1:row.opacity);layerX.text=String(row.x===undefined?0.5:row.x);layerY.text=String(row.y===undefined?0.5:row.y);objectChoice.currentIndex=Math.max(0,objectChoice.model.findIndex(o=>o.id===row.object_id))}
 function keepLayer(){if(!path.text.trim())return;var row={path:path.text,start_ms:start.value,end_ms:end.value,x:Number(layerX.text),y:Number(layerY.text),scale:Number(layerScale.text),opacity:Number(layerOpacity.text),object_id:objectChoice.model[objectChoice.currentIndex].id};var copy=layers.slice();if(selected>=0){if(copy[selected].id)row.id=copy[selected].id;copy[selected]=row}else copy.push(row);layers=copy;selected=-1;clearLayer()}
 contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

  ComboBox{id:mode;objectName:"storyboardAnimationMode";property var values:["hold","layers","camera"];model:["静帧保持与切换","局部图层运动","摄影机运动预演"]}
  Label{text:"图层可绑定场面对象，随动作轨移动；摄影机模式对平面素描做平移与缩放，不生成新的侧面。导出为无标注视频。";wrapMode:Text.Wrap;Layout.fillWidth:true}
  ComboBox{model:editor.layers.map((l,i)=>({label:(i+1)+" · "+((desk.board.objects||[]).find(o=>o.id===l.object_id)?.name||"固定画面"),index:i}));textRole:"label";Layout.fillWidth:true;displayText:editor.selected<0?"选择已有图层":currentText;onActivated:editor.loadLayer(currentIndex)}
  RowLayout{Layout.fillWidth:true;TextField{id:path;objectName:"storyboardLayerPath";placeholderText:"透明图层图片";Layout.fillWidth:true}Button{text:"浏览";onClicked:picker.open()}}
  ComboBox{id:objectChoice;objectName:"storyboardLayerObject";model:[{id:null,name:"固定于画面"}].concat(desk.board.objects||[]);textRole:"name";Layout.fillWidth:true}
  GridLayout{columns:4;Layout.fillWidth:true
   Label{text:"镜内起点"}SpinBox{id:start;from:0;to:2147483647;editable:true}Label{text:"终点"}SpinBox{id:end;from:1;to:2147483647;editable:true}
   Label{text:"横向"}TextField{id:layerX;objectName:"storyboardLayerX";validator:DoubleValidator{} Layout.preferredWidth:100}Label{text:"纵向"}TextField{id:layerY;objectName:"storyboardLayerY";validator:DoubleValidator{} Layout.preferredWidth:100}
   Label{text:"缩放"}TextField{id:layerScale;objectName:"storyboardLayerScale";validator:DoubleValidator{bottom:0.000001} Layout.preferredWidth:100}Label{text:"不透明度"}TextField{id:layerOpacity;objectName:"storyboardLayerOpacity";validator:DoubleValidator{bottom:0;top:1} Layout.preferredWidth:100}
  }
  RowLayout{Button{objectName:"storyboardKeepLayer";text:editor.selected>=0?"更新图层":"添加图层";enabled:!!path.text.trim();onClicked:editor.keepLayer()}Button{text:"删除所选图层";enabled:editor.selected>=0;onClicked:{var copy=editor.layers.slice();copy.splice(editor.selected,1);editor.layers=copy;editor.selected=-1;editor.clearLayer()}}}
  Label{visible:!!desk.message;text:desk.message;color:desk.t.warn;wrapMode:Text.Wrap;Layout.fillWidth:true}
  RowLayout{CheckBox{text:"导出含临时声音";checked:desk.exportIncludeAudio;onToggled:desk.exportIncludeAudio=checked}ComboBox{model:["素描预演","风格预演"];onActivated:desk.exportVariant=currentIndex?"style":"sketch"}}
  Label{text:"导出使用已保存的动画设置。";color:desk.t.muted}
  RowLayout{Item{Layout.fillWidth:true}Button{objectName:"storyboardSaveAnimation";text:"保存动画设置";enabled:!desk.busy&&!desk.approved;onClicked:{editor.keepLayer();desk.mediaAction("set_storyboard_animation",{unit_id:desk.unitId,shot_id:editor.shotId,revision:editor.revision,mode:mode.values[mode.currentIndex],layers:editor.layers})}}Button{objectName:"storyboardExportAnimatic";text:"导出干净预演…";enabled:!desk.busy;onClicked:desk.exportAnimatic()}}
 }
 FileDialog{id:picker;title:"选择透明图层";nameFilters:["图片 (*.png *.webp)"];onAccepted:path.text=selectedFile.toString()}
}
