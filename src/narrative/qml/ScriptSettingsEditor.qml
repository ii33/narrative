import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id:editor
    property var host
    property string errorText:""
    readonly property var t:host.t
    title:"剧本创作要求"
    modal:true;anchors.centerIn:parent;width:Math.min(650,host.width-40)
    function openFor(){titleField.text=host.screenplay.title || "";language.text=host.screenplay.language || "zh-CN";brief.text=JSON.stringify(host.screenplay.creative_brief||{},null,2);errorText="";open()}
    contentItem:ColumnLayout{
        enabled:!editor.host.structureBusy
        Label{visible:!!editor.errorText;text:editor.errorText;color:t.bad;Layout.fillWidth:true;wrapMode:Text.Wrap}
        TextField{id:titleField;placeholderText:"剧本名称";Layout.fillWidth:true}
        TextField{id:language;placeholderText:"语言";Layout.fillWidth:true}
        Label{text:"创作要求（JSON对象：风格、受众、改编约束等）";color:t.muted}
        TextArea{id:brief;Layout.fillWidth:true;Layout.preferredHeight:180;wrapMode:TextEdit.Wrap}
        Button{text:"保存";onClicked:{try{editor.host.structure([{op:"update_screenplay",expected_revision:editor.host.screenplay.revision,changes:{title:titleField.text,language:language.text,creative_brief:JSON.parse(brief.text)}}]);editor.close()}catch(error){editor.errorText=String(error)}}}
    }
}
