import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    SubmissionState{id:contentSubmission;owner:editor;operation:"save_compile_settings";target:({});onRestoreRequested:function(a){editor.draft=a.settings;editor.revision=a.expected_revision;editor.loaded=true;fontFamily.editText=a.settings.font_family;fontSize.text=String(a.settings.font_size_pt);lineHeight.text=String(a.settings.line_height)}}

    objectName: "scriptCompileSettings"
    property var host
    property var draft: ({})
    property var defaults: ({})
    property int revision: 0
    property int requestId: 0
    property string projectPath: ""
    readonly property string currentProjectPath: host.app.project.path || ""
    onCurrentProjectPathChanged: if (projectPath && currentProjectPath !== projectPath) { requestId++; close(); loaded = false; busy = false; draft = ({}) }
    property bool busy: false
    property bool loaded: false
    property string message: ""
    readonly property var kinds: ["scene", "speaker", "dialogue", "action", "scene_description"]
    readonly property var labels: ["场景标题", "说话人", "对白", "动作", "场景描写"]
    readonly property var alignments: ["left", "center", "right", "justify"]
    title: "剧本编译排版设置"
    modal: true
    anchors.centerIn: parent
    width: Math.min(900, host.width - 32)
    height: Math.min(760, host.height - 24)
    function openSettings() {
        if(visible&&!contentSubmission.restoring)contentSubmission.begin();
        requestId++; projectPath = host.app.project.path || ""; loaded = false; busy = true; message = ""
        open(); host.act("load_compile_settings", {_view_request_token: requestId})
    }
    function valid() {
        if (!loaded || !fontFamily.editText.trim() || !fontSize.acceptableInput || !lineHeight.acceptableInput) return false
        for (var i = 0; i < numbers.count; i++) if (!numbers.itemAt(i).valid) return false
        return true
    }
    function save() {
        if (!valid() || projectPath !== (host.app.project.path || "")) return
        var value = JSON.parse(JSON.stringify(draft))
        value.font_family = fontFamily.editText; value.font_size_pt = Number(fontSize.text); value.line_height = Number(lineHeight.text)
        for (var i = 0; i < numbers.count; i++) numbers.itemAt(i).apply(value)
        for (var j = 0; j < alignRows.count; j++) value.paragraphs[kinds[j]].alignment = alignRows.itemAt(j).alignment
        busy = true; message = ""
        host.act("save_compile_settings", {settings: value, expected_revision: revision, reason: "修改剧本编译排版", _view_request_token: requestId})
    }
    readonly property var numericFields: {
        var fields = []
        ;[{key:"top",label:"上"},{key:"right",label:"右"},{key:"bottom",label:"下"},{key:"left",label:"左"}].forEach(function(m){fields.push({section:"page_margins_mm",key:m.key,label:"页边距 · "+m.label+"（mm）"})})
        kinds.forEach(function(kind,index){[{key:"indent_left_mm",label:"左缩进（mm）"},{key:"indent_right_mm",label:"右缩进（mm）"},{key:"space_before_pt",label:"段前（pt）"},{key:"space_after_pt",label:"段后（pt）"}].forEach(function(f){fields.push({section:kind,key:f.key,label:labels[index]+" · "+f.label})})})
        return fields
    }
    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind !== "screenplay" || ["load_compile_settings", "save_compile_settings"].indexOf(payload.operation) < 0) return
            if (!editor.visible || payload.request_token !== editor.requestId || editor.projectPath !== (editor.host.app.project.path || "")) return
            var result = payload.result || {}
            editor.busy = false; editor.loaded = true; editor.revision = result.revision
            editor.defaults = result.defaults || {}; editor.draft = result.settings || {}
            if (payload.operation === "save_compile_settings") {
                editor.message = "已保存到当前工程，编译预览已更新。"
                if (editor.host.exportScreenplayId === editor.host.screenplay.id)
                    editor.host.act("compile_screenplay", {screenplay_id: editor.host.exportScreenplayId, format: "html"})
            }
        }
        function onErrorOccurred(kind, text) {
            if (kind === "screenplay" && editor.visible && editor.busy) { editor.busy = false; editor.message = text }
        }
    }
    contentItem: ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        Label { text: "这些设置供本工程所有剧本共享，用于预览及 HTML、PDF、Word 输出，不修改正文。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            ColumnLayout {
                width: parent.width; enabled: editor.loaded && !editor.busy
                Label { text: "字体" }
                ComboBox { id: fontFamily; objectName: "scriptCompileFont"; editable: true; model: Qt.fontFamilies(); editText: editor.draft.font_family || ""; Layout.fillWidth: true }
                RowLayout {
                    Label { text: "字号（pt）" }
                    TextField { id: fontSize; objectName: "scriptCompileFontSize"; text: editor.draft.font_size_pt === undefined ? "" : String(editor.draft.font_size_pt); validator: DoubleValidator { bottom: 6; top: 40; locale: "C" } Layout.preferredWidth: 100 }
                    Label { text: "行距（倍）" }
                    TextField { id: lineHeight; objectName: "scriptCompileLineHeight"; text: editor.draft.line_height === undefined ? "" : String(editor.draft.line_height); validator: DoubleValidator { bottom: 1; top: 4; locale: "C" } Layout.preferredWidth: 100 }
                }
                Label { text: "段落对齐"; font.bold: true }
                RowLayout {
                    Repeater {
                        id: alignRows; model: editor.kinds
                        ColumnLayout {
                            required property string modelData
                            required property int index
                            readonly property string alignment: editor.alignments[align.currentIndex]
                            Label { text: editor.labels[parent.index] }
                            ComboBox { id: align; objectName: "scriptCompileAlignment_" + parent.modelData; model: ["左对齐", "居中", "右对齐", "两端对齐"]; currentIndex: Math.max(0, editor.alignments.indexOf(((editor.draft.paragraphs || {})[parent.modelData] || {}).alignment)) }
                        }
                    }
                }
                GridLayout {
                    columns: 2; Layout.fillWidth: true
                    Repeater {
                        id: numbers; model: editor.numericFields
                        RowLayout {
                            id: row
                            required property var modelData
                            readonly property var source: modelData.section === "page_margins_mm" ? (editor.draft.page_margins_mm || {}) : ((editor.draft.paragraphs || {})[modelData.section] || {})
                            readonly property bool valid: input.acceptableInput
                            function apply(value) { var part = modelData.section === "page_margins_mm" ? value.page_margins_mm : value.paragraphs[modelData.section]; part[modelData.key] = Number(input.text) }
                            Label { text: row.modelData.label; Layout.preferredWidth: 190 }
                            TextField { id: input; objectName: "scriptCompile_" + row.modelData.section + "_" + row.modelData.key; text: row.source[row.modelData.key] === undefined ? "" : String(row.source[row.modelData.key]); validator: DoubleValidator { bottom: 0; top: row.modelData.section === "page_margins_mm" ? 80 : 100; locale: "C" } Layout.preferredWidth: 85 }
                        }
                    }
                }
            }
        }
        Label { text: editor.busy ? "正在处理排版设置…" : editor.message; visible: !!text; wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            Button { objectName: "scriptCompileDefaults"; text: "填入默认值"; enabled: editor.loaded && !editor.busy; onClicked: editor.draft = JSON.parse(JSON.stringify(editor.defaults)) }
            Item { Layout.fillWidth: true }
            Button { text: "关闭"; enabled: !editor.busy; onClicked: editor.close() }
            Button { objectName: "scriptCompileSettingsSave"; text: "保存并应用"; enabled: !editor.busy && editor.valid(); onClicked: editor.save() }
        }
    }
}
