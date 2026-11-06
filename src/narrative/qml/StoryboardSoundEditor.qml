import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog{
 id:editor;objectName:"storyboardSoundDialog";property var desk;property var record:({});property string shotId:"";property int revision:0
    SubmissionState{id:contentSubmission;allowedActions:[];authoredFields:[];owner:editor;operation:"";target:({unit_id:editor.desk.unitId||null})}

 title:"临时声音";modal:true;anchors.centerIn:parent;width:Math.min(560,desk.width-24)
 function openFor(row){record=row||{};shotId=desk.shot.id;revision=desk.board.revision;kind.currentIndex=Math.max(0,kind.values.indexOf(record.kind||"effect"));labelField.text=record.label||"";path.text=record.audio_path||"";time.value=record.time_ms===undefined?Math.max(0,desk.localTime):record.time_ms;duration.value=record.duration_ms||1000;sourceIn.value=record.source_in_ms||0;anchor.currentIndex=record.marker_id?1:0;marker.currentIndex=Math.max(0,(desk.board.markers||[]).findIndex(m=>m.id===record.marker_id));open()}
 contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

  Label{text:"声音跟随共同播放头；音频从本地文件导入，静默段只标记停顿。";wrapMode:Text.Wrap;Layout.fillWidth:true}
  ComboBox{id:kind;objectName:"storyboardSoundKind";property var values:["dialogue","effect","silence"];model:["临时对白","效果声","静默"]}
  TextField{id:labelField;objectName:"storyboardSoundLabel";placeholderText:"声音说明";Layout.fillWidth:true}
  RowLayout{visible:kind.currentIndex!==2;Layout.fillWidth:true;TextField{id:path;objectName:"storyboardSoundPath";placeholderText:"选择本地音频文件";Layout.fillWidth:true}Button{text:"浏览";onClicked:desk.chooseSoundFile()}}
  ComboBox{id:anchor;objectName:"storyboardSoundAnchor";model:["镜内自由时间","跟随命名时刻"]}
  RowLayout{visible:anchor.currentIndex===0;Label{text:"镜内开始（毫秒）"}SpinBox{id:time;objectName:"storyboardSoundTime";from:0;to:2147483647;editable:true}}
  ComboBox{id:marker;visible:anchor.currentIndex===1;model:editor.desk.board.markers||[];textRole:"name";Layout.fillWidth:true}
  RowLayout{Label{text:"持续（毫秒）"}SpinBox{id:duration;objectName:"storyboardSoundDuration";from:1;to:2147483647;value:1000;editable:true}}
  RowLayout{visible:kind.currentIndex!==2;Label{text:"音频入点（毫秒）"}SpinBox{id:sourceIn;from:0;to:2147483647;editable:true}}
  Label{visible:!!desk.message;text:desk.message;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
  Button{objectName:"storyboardSaveSound";text:"保存声音";enabled:!desk.busy&&(kind.currentIndex===2||!!path.text.trim())&&(anchor.currentIndex===0||(desk.board.markers||[]).length>0);onClicked:{var change={op:record.id?"update_sound":"create_sound",shot_id:editor.shotId,kind:kind.values[kind.currentIndex],label:labelField.text,duration_ms:duration.value,source_in_ms:kind.currentIndex===2?0:sourceIn.value};if(record.id)change.sound_id=record.id;if(kind.currentIndex!==2){change.audio_path=path.text;change.source_in_ms=sourceIn.value}if(anchor.currentIndex===1)change.marker_id=(marker.model[marker.currentIndex]||{}).id;else change.time_ms=time.value;desk.savePhase2([change],revision);}}
 }
 function acceptPath(value){path.text=value}
}
