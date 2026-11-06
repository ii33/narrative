import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    SubmissionState{id:contentSubmission;identityKey:editor.creating?"new":editor.unit.id||"";owner:editor;operation:"apply_scene_table_changes";target:({segment_id:editor.host.segment.id||null});onRestoreRequested:function(a){var c=(a.changes||[])[0]||{};editor.openFor(c.op==="insert"?c.unit:Object.assign({id:c.unit_id},c.changes||{}))}}

    objectName: "scriptPlanEditor"
    property var host
    property var unit: ({})
    property var plan: ({})
    property bool creating: false
    property string errorText: ""
    property bool showAdvanced: false
    readonly property var t: host.t
    modal: true; width: Math.min(820, host.width-40); height: Math.min(740,host.height-30); anchors.centerIn: parent
    title: creating ? "新增场次 / 画面段" : "编辑场表 · " + (unit.plan.title || "未命名场次")
    function openFor(value) {
        if(visible&&!contentSubmission.restoring)contentSubmission.begin();
        unit=value || ({}); creating=!unit.id; plan=Object.assign({},unit.plan || {})
        kind.currentIndex=unit.kind === "screen_segment" ? 1:0; titleField.text=plan.title || ""; summary.text=plan.summary || ""
        location.text=plan.location_text || ""; time.text=plan.time_label || ""; number.text=unit.scene_number || ""; enabledBox.checked=unit.enabled !== false
        interior.currentIndex=Math.max(0,interior.model.indexOf(plan.interior_exterior || "UNSPECIFIED")); duration.text=plan.estimated_duration_seconds ? String(plan.estimated_duration_seconds) : ""
        cast.text=(plan.cast_ids || []).join(", "); locationId.text=plan.location_id || ""; goal.text=plan.goal || ""; conflict.text=plan.conflict || ""; outcome.text=plan.outcome || ""; notes.text=plan.production_notes || ""
        mode.currentIndex=Math.max(0,mode.model.indexOf(plan.narrative_mode || "normal")); presentation.currentIndex=Math.max(0,presentation.model.indexOf(plan.presentation || "title_card"))
        continuity.text=JSON.stringify(plan.continuity_requirements || []); storyTime.text=plan.story_time ? JSON.stringify(plan.story_time) : ""
        showAdvanced=!!notes.text.trim()||(plan.continuity_requirements||[]).length>0||!!plan.story_time
        errorText=""; open()
    }
    function save() {
        try {
            var p=Object.assign({},plan,{title:titleField.text,summary:summary.text,interior_exterior:interior.currentText,location_text:location.text,location_id:locationId.text.trim() || null,time_label:time.text,cast_ids:cast.text.split(/[,，]/).map(function(x){return x.trim()}).filter(Boolean),goal:goal.text,conflict:conflict.text,outcome:outcome.text,production_notes:notes.text,narrative_mode:mode.currentText,estimated_duration_seconds:duration.text ? Number(duration.text) : null,continuity_requirements:JSON.parse(continuity.text || "[]"),story_time:storyTime.text ? JSON.parse(storyTime.text) : null})
            if (kind.currentIndex === 1) p.presentation=presentation.currentText
            else delete p.presentation
            if (!p.summary.trim()) { errorText="请填写本场概要"; return }
            var value={kind:kind.currentIndex===1?"screen_segment":"scene",plan:p,enabled:enabledBox.checked,scene_number:number.text || null}
            host.changeSceneTable(creating ? {op:"insert",unit:value,after_id:host.selectedUnitId || null} : {op:"update",unit_id:unit.id,changes:value})
        } catch(error) { errorText=String(error) }
    }
    contentItem: ScrollView {
        clip: true
        ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

            width: parent.width; spacing: 8
            Label { visible: !!editor.errorText; text: editor.errorText; color: t.bad; wrapMode: Text.Wrap; Layout.fillWidth:true }
            Label {objectName:"scriptSceneTableGuide";text:"按创作需要编写或修改内容。对话使用工程设置中的提示词，已有内容可反复编辑。场表所有字段都会供写作参考，请与当前事件线核对。";color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
            RowLayout { ComboBox { id: kind; model:["普通场景","独立画面段"] } TextField { id:number; placeholderText:"场号"; Layout.preferredWidth:100 } CheckBox { id:enabledBox; text:"纳入输出与批量" } }
            TextField { id:titleField; objectName:"scriptPlanTitle"; placeholderText:"场表标题"; Layout.fillWidth:true }
            TextArea { id:summary; objectName:"scriptPlanSummary"; placeholderText:"剧情概要（必填）"; wrapMode:TextEdit.Wrap; Layout.fillWidth:true; Layout.preferredHeight:90 }
            RowLayout { ComboBox { id:interior; model:["UNSPECIFIED","INT","EXT","INT_EXT"] } TextField { id:location; placeholderText:"地点文字"; Layout.fillWidth:true } TextField { id:time; placeholderText:"时间，如夜 / 连续"; Layout.fillWidth:true } }
            TextField { id:locationId; objectName:"scriptPlanLocationId"; placeholderText:"地点实体ID（可选）"; Layout.fillWidth:true }
            TextField { id:cast; placeholderText:"人物/群体ID，逗号分隔（草稿可不绑定）"; Layout.fillWidth:true }
            RowLayout { TextField { id:duration; placeholderText:"预计秒数"; validator:DoubleValidator { bottom:0.01 } Layout.fillWidth:true } ComboBox { id:mode; model:["normal","flashback","dream","imagined"] } ComboBox { id:presentation; visible:kind.currentIndex===1; model:["title_card","black_screen","credits","other"] } }
            TextField { id:goal; placeholderText:"目标"; Layout.fillWidth:true }
            TextField { id:conflict; placeholderText:"冲突"; Layout.fillWidth:true }
            Label {text:"结果（可选）";color:t.ink}
            TextArea { id:outcome; objectName:"scriptPlanOutcome"; placeholderText:"本场之后的结果或变化";wrapMode:TextEdit.Wrap;Layout.preferredHeight:60;Layout.fillWidth:true }
            Button {objectName:"scriptPlanAdvancedToggle";text:editor.showAdvanced?"收起来源与可选信息":"来源与可选信息";onClicked:editor.showAdvanced=!editor.showAdvanced}
            ColumnLayout {
                objectName:"scriptPlanAdvanced";visible:editor.showAdvanced;Layout.fillWidth:true
                Label {visible:(editor.plan.source_event_ids||[]).length>0;text:"来源事件："+(editor.plan.source_event_ids||[]).join("、");color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
                TextArea {id:notes;objectName:"scriptPlanProductionNotes";placeholderText:"创作备注（可选）";wrapMode:TextEdit.Wrap;Layout.fillWidth:true}
                Label {text:"仅明确的人物/物品状态连续性（可选JSON数组）";color:t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true}
                TextField {id:continuity;objectName:"scriptPlanContinuity";Layout.fillWidth:true}
                TextField {id:storyTime;objectName:"scriptPlanStoryTime";placeholderText:"可选故事时间（JSON对象）";Layout.fillWidth:true}
            }
            RowLayout { Item { Layout.fillWidth:true } Button { text:"取消"; onClicked:editor.close() } Button { objectName:"scriptPlanSave"; text:"保存场表"; onClicked:editor.save() } }
        }
    }
}


