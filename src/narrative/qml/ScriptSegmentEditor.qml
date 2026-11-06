import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id:editor
    SubmissionState{id:contentSubmission;owner:editor;operation:"update_script_segment";target:({segment_id:editor.host.segment.id||null});closeOnAccepted:true;onRestoreRequested:function(a){var c=a.changes||{};titleField.text=c.title||"";brief.text=JSON.stringify(c.brief||{},null,2);duration.text=c.target_duration_seconds?String(c.target_duration_seconds):"";enabledBox.checked=c.enabled!==false;role.currentIndex=Math.max(0,role.model.indexOf(c.role||"normal"))}}

    property var host
    property string errorText:""
    readonly property var t:host.t
    title:"段落设置 · "+(host.segment.title || "")
    modal:true;anchors.centerIn:parent;width:Math.min(650,host.width-40)
    function openFor(){titleField.text=host.segment.title || "";brief.text=JSON.stringify(host.segment.brief || {},null,2);duration.text=host.segment.target_duration_seconds?String(host.segment.target_duration_seconds):"";enabledBox.checked=host.segment.enabled!==false;role.currentIndex=Math.max(0,role.model.indexOf(host.segment.role || "normal"));errorText="";open()}
    contentItem:ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        Label{visible:!!editor.errorText;text:editor.errorText;color:t.bad;wrapMode:Text.Wrap;Layout.fillWidth:true}
        TextField{id:titleField;placeholderText:"段落名称";Layout.fillWidth:true}
        RowLayout{ComboBox{id:role;model:["normal","prologue","epilogue"]}CheckBox{id:enabledBox;text:"启用段落"}TextField{id:duration;placeholderText:"目标秒数";validator:DoubleValidator{bottom:0.01}}}
        Label{text:"段落创作要求（JSON对象）";color:t.muted}
        TextArea{id:brief;wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.preferredHeight:150}
        Button{text:"用当前连续选区替换绑定（保留正文）";enabled:editor.host.rangePreview.status==="valid";onClicked:{editor.host.rebindSegment()}}
        RowLayout{Item{Layout.fillWidth:true}Button{text:"取消";onClicked:editor.close()}Button{text:"保存";onClicked:{try{editor.host.act("update_script_segment",{segment_id:editor.host.segment.id,expected_revision:editor.host.segment.revision,changes:{title:titleField.text,brief:JSON.parse(brief.text),target_duration_seconds:duration.text?Number(duration.text):null,enabled:enabledBox.checked,role:role.currentText},reason:"编辑段落要求"})}catch(error){editor.errorText=String(error)}}}}
    }
}


