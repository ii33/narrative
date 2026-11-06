import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Dialog{
 id:editor;objectName:"storyboardMarkersDialog";property var desk;property var record:({});property int revision:0
    SubmissionState{id:contentSubmission;allowedActions:["create_marker", "update_marker"];authoredFields:[];owner:editor;operation:"apply_storyboard_changes";target:({unit_id:editor.desk.unitId||null});onRestoreRequested:function(a){var c=a.changes[0];editor.openFor({id:c.marker_id,name:c.name,time_ms:c.time_ms});editor.revision=a.revision}}

 title:"命名时刻 · 场内动作时间";modal:true;anchors.centerIn:parent;width:Math.min(620,desk.width-24);height:Math.min(540,desk.height-24)
 function openFor(row){if(visible&&!contentSubmission.restoring)contentSubmission.begin();record=row||{};revision=desk.board.revision;nameField.text=record.name||"";time.value=record.time_ms===undefined?desk.actionTime:record.time_ms;open()}
 contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

  Label{text:"命名时刻可由多个机位、素描、动作点和声音共同引用；移动前会列出影响。";wrapMode:Text.Wrap;Layout.fillWidth:true}
  ListView{Layout.fillWidth:true;Layout.fillHeight:true;clip:true;model:desk.board.markers||[]
   delegate:RowLayout{required property var modelData;width:ListView.view.width;Button{Layout.fillWidth:true;text:modelData.name+" · "+desk.seconds(modelData.time_ms);onClicked:desk.seekMarker(modelData)}Button{text:"编辑";onClicked:editor.openFor(modelData)}Button{text:"删除";enabled:!desk.busy&&!desk.approved;onClicked:desk.savePhase2([{op:"delete_marker",marker_id:modelData.id}],desk.board.revision)}}
  }
  TextField{id:nameField;objectName:"storyboardMarkerName";placeholderText:"如指节碰到玻璃";Layout.fillWidth:true}
  RowLayout{Label{text:"动作时刻（毫秒）"}SpinBox{id:time;objectName:"storyboardMarkerTime";from:0;to:2147483647;editable:true}Button{text:"当前播放位置";onClicked:time.value=desk.actionTime}}
  Label{visible:!!desk.message;text:desk.message;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
  RowLayout{Button{text:"新增另一时刻";onClicked:editor.openFor({})}Item{Layout.fillWidth:true}Button{objectName:"storyboardSaveMarker";text:record.id?"保存修改":"新增时刻";enabled:!!nameField.text.trim()&&!desk.busy&&!desk.approved;onClicked:{var c={op:record.id?"update_marker":"create_marker",name:nameField.text.trim(),time_ms:time.value};if(record.id)c.marker_id=record.id;if(record.id&&record.time_ms!==time.value)desk.previewPhase2([c],revision);else desk.savePhase2([c],revision)}}}
 }
}
