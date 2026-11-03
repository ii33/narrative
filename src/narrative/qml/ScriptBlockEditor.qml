import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    objectName: "scriptBlockEditor"
    property var host
    property var block: ({})
    property string errorText: ""
    readonly property var t: host.t
    title: block.id ? "修改" + (host.blockLabels[block.type] || "正文块") : "新增正文块"
    modal:true; width:Math.min(760,host.width-40); height:Math.min(570,host.height-30); anchors.centerIn:parent
    function openFor(value) {
        block=value || ({}); var p=block.payload || {}; kind.currentIndex=Math.max(0,kind.values.indexOf(block.type || "action"))
        textField.text=p.text || ""; speaker.text=p.speaker_label || ""; speakerId.text=p.speaker_id || ""; parenthetical.text=p.parenthetical || ""
        delivery.currentIndex=Math.max(0,delivery.model.indexOf(p.delivery || "on_screen")); soundCategory.currentIndex=Math.max(0,soundCategory.model.indexOf(p.category || "effect")); screenStyle.currentIndex=Math.max(0,screenStyle.model.indexOf(p.presentation || "overlay")); transition.text=p.kind || "cut"; entityId.text=p.entity_id || ""; errorText=""; open()
    }
    function save() {
        var type=kind.values[kind.currentIndex], p={text:textField.text}
        if (type==="dialogue") p={speaker_id:speakerId.text.trim() || null,speaker_label:speaker.text,text:textField.text,delivery:delivery.currentText,parenthetical:parenthetical.text}
        if (type==="screen_text") p.presentation=screenStyle.currentText
        if (type==="sound") { p.category=soundCategory.currentText; if(entityId.text.trim()) p.entity_id=entityId.text.trim() }
        if (type==="transition") p.kind=transition.text
        host.changeBlock(block.id ? {op:"update",block_id:block.id,block:{type:type,payload:p}} : {op:"insert",block:{type:type,payload:p},after_id:host.lastBlockId()})
    }
    contentItem:ColumnLayout {
        spacing:10
        Label { visible:!!editor.errorText; text:editor.errorText; color:t.bad; wrapMode:Text.Wrap; Layout.fillWidth:true }
        ComboBox { id:kind; objectName:"scriptBlockType"; property var values:["action","dialogue","screen_text","sound","transition"]; model:["动作","对白","字幕","声音","转场"] }
        RowLayout { visible:kind.values[kind.currentIndex]==="dialogue"; Layout.fillWidth:true; TextField { id:speaker; placeholderText:"发声者名称"; Layout.fillWidth:true } TextField { id:speakerId; placeholderText:"人物/群体ID（可选）"; Layout.fillWidth:true } }
        RowLayout { visible:kind.values[kind.currentIndex]==="dialogue"; Layout.fillWidth:true; ComboBox { id:delivery; model:["on_screen","off_screen","voice_over","inner_voice"] } TextField { id:parenthetical; placeholderText:"表演提示"; Layout.fillWidth:true } }
        RowLayout { visible:kind.values[kind.currentIndex]==="sound"; Layout.fillWidth:true; ComboBox { id:soundCategory; model:["effect","music","other"] } TextField { id:entityId; placeholderText:"声音实体ID（可选）"; Layout.fillWidth:true } }
        ComboBox { id:screenStyle; visible:kind.values[kind.currentIndex]==="screen_text"; model:["overlay","card"] }
        TextField { id:transition; visible:kind.values[kind.currentIndex]==="transition"; placeholderText:"转场类型，如fade_out"; Layout.fillWidth:true }
        TextArea { id:textField; objectName:"scriptBlockText"; placeholderText:"动作 / 对白 / 字幕 / 声音 / 转场文字"; wrapMode:TextEdit.Wrap; Layout.fillWidth:true; Layout.fillHeight:true }
        RowLayout { Item { Layout.fillWidth:true } Button { text:"取消"; onClicked:editor.close() } Button { objectName:"scriptBlockSave"; text:"保存正文块"; onClicked:editor.save() } }
    }
}

