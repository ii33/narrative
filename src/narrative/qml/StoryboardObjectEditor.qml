import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
Dialog{
 id:editor;objectName:"storyboardObjectDialog";property var desk;property bool creating:true;property var object:({});property bool pending:false;property string errorText:"";property string owner:"";property int revision:0
    SubmissionState{id:contentSubmission;allowedActions:["create_object", "update_object"];authoredFields:[];identityKey:editor.creating?"new":editor.object.id||"";owner:editor;operation:"apply_storyboard_changes";target:({unit_id:editor.owner||editor.desk.unitId||null});onRestoreRequested:function(a){var c=a.changes[0];editor.creating=c.op==="create_object";editor.object={id:c.object_id};editor.owner=a.unit_id;editor.revision=a.revision;nameField.text=c.name||"";kind.currentIndex=Math.max(0,kind.values.indexOf(c.kind));xField.text=String(c.x);yField.text=String(c.y);rotationField.text=String(c.rotation||0);heightField.text=String(c.height||0.2);elevationField.text=String(c.elevation||0);depthField.text=String(c.depth||0)}}

 title:creating?"添加场面对象":"场面对象属性";modal:true;anchors.centerIn:parent;width:Math.min(460,desk.width-24)
 function openFor(create){
        if(visible&&!contentSubmission.restoring)contentSubmission.begin();creating=create;object=(desk.board.objects||[]).find(o=>o.id===desk.selectedObjectId)||{};owner=desk.unitId;revision=desk.board.revision;pending=false;errorText="";nameField.text=create?"":object.name;kind.currentIndex=create?0:Math.max(0,kind.values.indexOf(object.kind));xField.text=String(create?0.5:object.x);yField.text=String(create?0.5:object.y);rotationField.text=String(create?0:object.rotation);heightField.text=String(create?0.2:(object.height===undefined?0.2:object.height));elevationField.text=String(create?0:(object.elevation||0));depthField.text=String(create?0:(object.depth||0));open()}
 contentItem:ColumnLayout{
        SubmissionFeedback{state:contentSubmission}

  Label{text:"这里编辑全场基础位置与朝向；已有动作关键点仍按时间覆盖基础值。当前时刻的修改请开启记录关键点。";wrapMode:Text.Wrap;Layout.fillWidth:true}
  Label{visible:!!editor.errorText;text:editor.errorText;wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.warn}
  TextField{id:nameField;objectName:"storyboardObjectName";placeholderText:"对象名称";Layout.fillWidth:true;enabled:!editor.pending}
  ComboBox{id:kind;objectName:"storyboardObjectKind";model:["人物","道具","布景"];property var values:["person","prop","set"];enabled:!editor.pending}
  GridLayout{columns:2;Layout.fillWidth:true;enabled:!editor.pending
   Label{text:"基础横向位置（0–1）"}TextField{id:xField;objectName:"storyboardObjectX";validator:DoubleValidator{bottom:0;top:1} Layout.fillWidth:true}
   Label{text:"基础纵向位置（0–1）"}TextField{id:yField;objectName:"storyboardObjectY";validator:DoubleValidator{bottom:0;top:1} Layout.fillWidth:true}
   Label{text:"基础朝向（度）"}TextField{id:rotationField;objectName:"storyboardObjectRotation";validator:DoubleValidator{} Layout.fillWidth:true}
   Label{text:"对象高度"}TextField{id:heightField;objectName:"storyboardObjectHeight";validator:DoubleValidator{bottom:0.000001} Layout.fillWidth:true}
   Label{text:"离地高度"}TextField{id:elevationField;objectName:"storyboardObjectElevation";validator:DoubleValidator{} Layout.fillWidth:true}
   Label{text:"调度板显示层"}TextField{id:depthField;objectName:"storyboardObjectDepth";validator:DoubleValidator{} Layout.fillWidth:true}
  }
  RowLayout{Item{Layout.fillWidth:true}Button{text:"取消";enabled:!editor.pending;onClicked:editor.close()}Button{objectName:"storyboardSaveObject";text:editor.pending?"保存中…":"保存";enabled:!!nameField.text.trim()&&xField.acceptableInput&&yField.acceptableInput&&rotationField.acceptableInput&&heightField.acceptableInput&&elevationField.acceptableInput&&depthField.acceptableInput&&!editor.pending&&!desk.busy;onClicked:{if(editor.owner!==desk.unitId)return;var change={op:editor.creating?"create_object":"update_object",name:nameField.text.trim(),kind:kind.values[kind.currentIndex],x:Number(xField.text),y:Number(yField.text),rotation:Number(rotationField.text),height:Number(heightField.text),elevation:Number(elevationField.text),depth:Number(depthField.text)};if(!editor.creating)change.object_id=editor.object.id;editor.pending=true;desk.focusShot=desk.shot.id;desk.focusOffset=desk.localTime;desk.request("apply_storyboard_changes",{unit_id:editor.owner,revision:editor.revision,changes:[change]})}}}
 }
}
