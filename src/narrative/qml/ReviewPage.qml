import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 审核：模型只能提案，人接受才进正式图。
Item {
    id: page
    objectName: "reviewPage"
    property var app
    readonly property var t: app.theme

    property string filter: "pending"
    property string selectedId: ""
    property var current: ({})
    property var auditIssues: []
    property int auditErrors: 0
    property int auditWarnings: 0
    property int auditInfos: 0
    property string auditScope: ""

    ListModel { id: candidateRows }
    ListModel { id: objectRows }

    // 两种候选并存：提取走的是「变更候选」（change_propose），旧的图候选还在老工程里。
    // 各自拉一次，合并成一张表，用 kind 区分。
    property var graphRows: []
    property var changeRows: []

    function load() {
        bridge.loadChangeCandidates(filter)
        bridge.loadCandidates(filter)
    }

    function rebuildCandidates() {
        candidateRows.clear()
        for (var i = 0; i < changeRows.length; ++i) {
            var c = changeRows[i]
            candidateRows.append({
                id: c.id || "",
                kind: "change",
                note: c.note || c.id || "",
                status: c.status || "",
                source: c.source || "",
                counts: (c.graph_count || 0) + " 图 · " + (c.property_count || 0) + " 属性",
                created: c.created_at || ""
            })
        }
        for (var j = 0; j < graphRows.length; ++j) {
            var g = graphRows[j]
            candidateRows.append({
                id: g.id || "",
                kind: "graph",
                note: g.note || g.id || "",
                status: g.status || "",
                source: g.source || "",
                counts: (g.operation_count || g.graph_count || 0) + " 项",
                created: g.created_at || ""
            })
        }
        if (candidateRows.count > 0 && selectedId.length === 0)
            page.pick(candidateRows.get(0).id)
    }

    function kindOf(id) {
        for (var i = 0; i < candidateRows.count; ++i)
            if (candidateRows.get(i).id === id) return candidateRows.get(i).kind
        return "graph"
    }

    function pick(id) {
        selectedId = id
        current = ({})
        if (kindOf(id) === "change") bridge.changeCandidateDetail(id)
        else bridge.candidateDetail(id)
    }

    function applyAudit(report, scope) {
        var issues = (report && report.issues) ? report.issues : []
        auditIssues = issues
        auditErrors = 0
        auditWarnings = 0
        auditInfos = 0
        for (var i = 0; i < issues.length; ++i) {
            if (issues[i].severity === "error") auditErrors += 1
            else if (issues[i].severity === "warning") auditWarnings += 1
            else auditInfos += 1
        }
        auditScope = scope
    }

    function rebuildObjects() {
        objectRows.clear()
        var objects = current.objects || []
        for (var i = 0; i < objects.length; ++i) {
            var obj = objects[i]
            objectRows.append({
                id: obj.id || "",
                kind: obj.kind || "",
                label: obj.name || obj.label || obj.id || "",
                extra: obj.node_type || obj.value_type || ""
            })
        }
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.load()

    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") page.load() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "candidates") {
                page.graphRows = payload.items || []
                page.rebuildCandidates()
            } else if (kind === "change_candidates") {
                page.changeRows = payload.items || []
                page.rebuildCandidates()
            } else if (kind === "candidate") {
                current = payload
                page.rebuildObjects()
            } else if (kind === "change_candidate") {
                var detail = payload.candidate || ({})
                current = {
                    candidate: detail,
                    objects: detail.graph_objects || [],
                    kind: "change"
                }
                page.rebuildObjects()
            } else if (kind === "change_review") {
                app.toast("已处理，修订号 " + (payload.revision || ""), false)
                selectedId = ""
                page.load()
            } else if (kind === "property_audit") {
                page.applyAudit(payload, "全部属性")
                app.toast("审核完成：" + page.auditErrors + " 个错误 / "
                          + page.auditWarnings + " 个待确认", page.auditErrors > 0)
            } else if (kind === "extraction_commit") {
                var took = payload.accepted || null
                var left = (payload.doubtful || []).length
                app.toast(took
                    ? ("已进图 " + took.node_count + " 个节点，留 " + left + " 处待确认")
                    : ("留 " + left + " 处待确认"), left > 0)
                page.load()
            } else if (kind === "review") {
                app.toast("已处理，修订号 " + (payload.revision || ""), false)
                page.load()
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Rectangle {
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            color: t.card
            border.color: t.line
            radius: t.radius

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "候选"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: t.ink
                    }
                    Item { Layout.fillWidth: true }
                    ComboBox {
                        Layout.preferredWidth: 100
                        model: ["待审", "已接受", "已拒绝"]
                        currentIndex: 0
                        onActivated: {
                            page.filter = ["pending", "accepted", "rejected"][currentIndex]
                            page.load()
                        }
                    }
                }

                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: candidateRows
                    spacing: 4
                    delegate: Rectangle {
                        required property string id
                        required property string kind
                        required property string note
                        required property string status
                        required property string source
                        required property string counts
                        width: ListView.view.width
                        height: 52
                        radius: 6
                        color: page.selectedId === id ? t.accentSoft : "transparent"
                        Column {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Label {
                                text: note
                                color: t.ink
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                width: 220
                            }
                            Label {
                                text: (kind === "change" ? "变更" : "图") + " · " + status
                                    + " · " + counts
                                color: t.muted
                                font.pixelSize: 10
                                elide: Text.ElideRight
                                width: 220
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.pick(id)
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: candidateRows.count === 0
                        text: "没有候选。提取出来的候选会出现在这里。"
                        color: t.muted
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: current.candidate
                        ? "候选 " + current.candidate.id + " · " + current.candidate.status
                        : "未选择候选"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    color: t.ink
                }
                Item { Layout.fillWidth: true }
                Button {
                    objectName: "acceptCandidateButton"
                    text: "接受"
                    highlighted: true
                    enabled: current.candidate !== undefined && current.candidate !== null
                    onClicked: current.kind === "change"
                        ? bridge.acceptChangeCandidate(current.candidate.id, current.candidate.version)
                        : bridge.acceptCandidate(current.candidate.id, current.candidate.version)
                }
                Button {
                    objectName: "rejectCandidateButton"
                    text: "拒绝"
                    enabled: current.candidate !== undefined && current.candidate !== null
                    onClicked: current.kind === "change"
                        ? bridge.rejectChangeCandidate(current.candidate.id, current.candidate.version)
                        : bridge.rejectCandidate(current.candidate.id, current.candidate.version)
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: objectRows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string kind
                        required property string label
                        required property string extra
                        width: ListView.view.width
                        height: 36
                        radius: 4
                        color: t.paper
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label {
                                text: id
                                color: t.muted
                                font.pixelSize: 11
                                Layout.preferredWidth: 60
                            }
                            Label {
                                text: kind
                                color: t.accent
                                font.pixelSize: 11
                                Layout.preferredWidth: 100
                            }
                            Label {
                                text: label
                                color: t.ink
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Label {
                                text: extra
                                color: t.muted
                                font.pixelSize: 11
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 190
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "属性审核"
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            color: t.ink
                        }
                        Label {
                            visible: page.auditScope.length > 0
                            text: page.auditScope
                            color: t.muted
                            font.pixelSize: 11
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            visible: page.auditIssues.length > 0
                            text: page.auditErrors + " 错误 · " + page.auditWarnings + " 待确认"
                                + (page.auditInfos > 0 ? " · " + page.auditInfos + " 提示" : "")
                            color: page.auditErrors > 0 ? t.bad : t.warn
                            font.pixelSize: 11
                        }
                        Button {
                            objectName: "auditPropertiesButton"
                            text: "审核全部属性"
                            flat: true
                            onClicked: bridge.auditProperties()
                        }
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.auditIssues
                        spacing: 2
                        delegate: Column {
                            required property var modelData
                            width: ListView.view.width
                            spacing: 0
                            Label {
                                width: parent.width
                                font.pixelSize: 11
                                wrapMode: Text.Wrap
                                color: modelData.severity === "error" ? t.bad
                                    : modelData.severity === "warning" ? t.warn : t.muted
                                text: (modelData.severity === "error" ? "错误 "
                                        : modelData.severity === "warning" ? "待确认 " : "提示 ")
                                    + (modelData.node_name || "") + " · " + (modelData.message || "")
                            }
                            Label {
                                width: parent.width
                                visible: (modelData.suggestion || "").length > 0
                                font.pixelSize: 10
                                color: t.muted
                                wrapMode: Text.Wrap
                                text: "→ " + (modelData.suggestion || "")
                            }
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: page.auditIssues.length === 0
                            text: "点「审核全部属性」检查重叠、矛盾、过时与遗漏。"
                            color: t.muted
                            font.pixelSize: 11
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 110
                visible: (current.diagnostics || []).length > 0
                color: t.card
                border.color: t.line
                radius: t.radius
                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: current.diagnostics || []
                    delegate: Label {
                        required property var modelData
                        width: ListView.view.width
                        height: 24
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        color: modelData.severity === "error" ? t.bad : t.warn
                        text: (modelData.code || "") + " " + (modelData.message || "")
                    }
                }
            }
        }
    }
}
