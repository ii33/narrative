import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 常驻协作者：只能读。问它图里的事，它自己调读工具，把用到的工具一行行摆出来。
// 面板不参与改图——接受候选、删除记录、改资料卡仍然是人在这里做。
Rectangle {
    id: panel
    objectName: "assistantPanel"
    property var app
    readonly property var t: app.theme

    signal closeRequested()

    // 一条消息：{role: "user"|"assistant", text, steps: [{tool, summary, ok}]}
    property var messages: []
    property bool busy: false
    property string draft: ""
    // 附图：本地路径 + 缩略图地址。发送时一起交给模型。
    property string attached: ""
    property string attachedUrl: ""

    function send() {
        var text = panel.draft.trim()
        if (text.length === 0 || panel.busy) return
        var next = panel.messages.slice()
        next.push({role: "user", text: text, steps: [], image: panel.attachedUrl})
        panel.messages = next
        var image = panel.attached
        panel.draft = ""
        panel.attached = ""
        panel.attachedUrl = ""
        panel.busy = true
        bridge.assistantTurn(text, image)
    }

    function attach(path) {
        panel.attached = path
        panel.attachedUrl = path ? "file:///" + String(path).replace(/\\/g, "/") : ""
    }

    function apply(payload) {
        panel.busy = false
        var next = panel.messages.slice()
        next.push({
            role: "assistant",
            text: payload.answer || "（没有回答）",
            steps: payload.steps || []
        })
        panel.messages = next
    }

    function fail(message) {
        panel.busy = false
        var next = panel.messages.slice()
        next.push({role: "assistant", text: message, steps: []})
        panel.messages = next
    }

    // 认一下它给的是哪种：成段的 HTML 用 RichText，其余按 Markdown 读。
    function richFormat(text) {
        var body = String(text || "")
        if (/<\s*(p|div|br|ul|ol|li|h[1-6]|table|strong|em|code|pre)\b/i.test(body))
            return Text.RichText
        return Text.MarkdownText
    }

    function reset() {
        panel.messages = []
        panel.busy = false
        bridge.assistantReset()
    }

    color: t.card
    border.color: t.line
    radius: t.radius

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Label {
                text: "协作者"
                color: t.ink
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Label {
                text: "只读"
                color: t.muted
                font.pixelSize: 10
            }
            Item { Layout.fillWidth: true }
            Label {
                objectName: "assistantReset"
                text: "清空"
                color: t.muted
                font.pixelSize: 10
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.reset()
                }
            }
            Label {
                objectName: "assistantClose"
                text: "收起"
                color: t.muted
                font.pixelSize: 10
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.closeRequested()
                }
            }
        }

        ListView {
            id: transcript
            objectName: "assistantTranscript"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 10
            model: panel.messages
            onCountChanged: positionViewAtEnd()

            delegate: ColumnLayout {
                required property var modelData
                width: ListView.view.width
                spacing: 3

                Label {
                    Layout.fillWidth: true
                    text: modelData.role === "user" ? "你" : "协作者"
                    color: modelData.role === "user" ? t.muted : t.accent
                    font.pixelSize: 9
                }
                Image {
                    visible: (modelData.image || "").length > 0
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 90
                    source: modelData.image || ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
                // 它查过什么：一行一个工具，折叠成一句话。
                Repeater {
                    model: modelData.steps
                    delegate: Label {
                        required property var modelData
                        Layout.fillWidth: true
                        text: "· " + modelData.tool
                            + (modelData.summary ? " → " + modelData.summary : "")
                        color: modelData.ok ? t.muted : t.bad
                        font.pixelSize: 9
                        elide: Text.ElideRight
                    }
                }
                // 回答可能是 Markdown 或一小段 HTML：按内容选渲染方式，别把星号原样显示。
                Label {
                    objectName: "assistantAnswer"
                    Layout.fillWidth: true
                    text: modelData.text
                    textFormat: panel.richFormat(modelData.text)
                    color: t.body
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: panel.messages.length === 0
            text: "问图里的事：某个场景里谁在场、某个编号是什么、某条记录为什么失效。"
            color: t.muted
            font.pixelSize: 10
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            visible: panel.attached.length > 0
            spacing: 6
            Image {
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40
                source: panel.attachedUrl
                fillMode: Image.PreserveAspectFit
                asynchronous: true
            }
            Label {
                Layout.fillWidth: true
                text: "已附一张图"
                color: t.muted
                font.pixelSize: 10
            }
            Label {
                objectName: "assistantDropImage"
                text: "移除"
                color: t.bad
                font.pixelSize: 10
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.attach("")
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Label {
                objectName: "assistantAttach"
                text: "图"
                color: t.muted
                font.pixelSize: 11
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: imagePicker.open()
                }
            }
            TextField {
                id: prompt
                objectName: "assistantPrompt"
                Layout.fillWidth: true
                placeholderText: panel.busy ? "它还在查…" : "问点什么，回车发送"
                text: panel.draft
                enabled: !panel.busy
                onTextChanged: panel.draft = text
                onAccepted: panel.send()
            }
            Button {
                objectName: "assistantSend"
                text: "发送"
                font.pixelSize: 11
                enabled: !panel.busy && panel.draft.trim().length > 0
                onClicked: panel.send()
            }
        }
    }

    FileDialog {
        id: imagePicker
        objectName: "assistantImagePicker"
        title: "选一张图给它看"
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: panel.attach(imagePicker.selectedFile.toString())
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "assistant") panel.apply(payload)
        }
        function onErrorOccurred(kind, message) {
            if (kind === "assistant") panel.fail(message)
        }
    }
}
