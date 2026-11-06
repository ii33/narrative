.pragma library

// Deterministic graph geometry. IDs only break otherwise equivalent ordering;
// no character, degree threshold, or graph centre has a reserved position.
// Count displayed semantic roles once, including symmetric reverse mirrors.
function relationKey(edge) {
    return JSON.stringify([String(edge.from), String(edge.to)].sort().concat([
        edge.kind || "", edge.state || "", edge.scope || "world",
        edge.holder_id || "", edge.context_id || "canon"]))
}

function layout(persons, links, radii, width, height) {
    var ids = persons.map(function(p) { return String(p.id) }).sort()
    var adjacent = Object.create(null), positions = Object.create(null), pairs = [], seen = Object.create(null), roles = Object.create(null)
    for (var i = 0; i < ids.length; ++i) adjacent[ids[i]] = []
    for (i = 0; i < links.length; ++i) {
        var a = String(links[i].from), b = String(links[i].to)
        if (a === b || !adjacent[a] || !adjacent[b]) continue
        var key = a < b ? JSON.stringify([a, b]) : JSON.stringify([b, a])
        if (!roles[key]) roles[key] = Object.create(null)
        roles[key][relationKey(links[i])] = true
        if (seen[key]) continue
        seen[key] = true
        pairs.push(a < b ? [a, b] : [b, a])
        adjacent[a].push(b); adjacent[b].push(a)
    }
    pairs.sort(function(a, b) { return JSON.stringify(a) < JSON.stringify(b) ? -1 : (JSON.stringify(a) > JSON.stringify(b) ? 1 : 0) })
    var components = [], visited = Object.create(null)
    for (i = 0; i < ids.length; ++i) {
        if (visited[ids[i]]) continue
        var queue = [ids[i]], component = []
        visited[ids[i]] = true
        for (var q = 0; q < queue.length; ++q) {
            var id = queue[q]
            component.push(id)
            var neighbors = adjacent[id].slice().sort()
            for (var k = 0; k < neighbors.length; ++k) {
                if (!visited[neighbors[k]]) { visited[neighbors[k]] = true; queue.push(neighbors[k]) }
            }
        }
        components.push(component.sort())
    }
    components.sort(function(a, b) { return b.length - a.length || (a[0] < b[0] ? -1 : (a[0] > b[0] ? 1 : 0)) })
    var boxes = [], area = 0, widest = 0
    for (var c = 0; c < components.length; ++c) {
        var members = components[c], local = Object.create(null), n = members.length
        for (i = 0; i < n; ++i) {
            var angle = 2 * Math.PI * i / Math.max(1, n)
            var seed = n === 1 ? 0 : 50 * Math.sqrt(n)
            local[members[i]] = {x: seed * Math.cos(angle), y: seed * Math.sin(angle),
                                  r: Math.max(1, Number(radii[members[i]]) || 24)}
        }
        var edges = pairs.filter(function(pair) { return local[pair[0]] && local[pair[1]] })
        var iterations = Math.max(180, Math.round(900 / Math.sqrt(Math.max(1, n / 12))))
        for (var step = 0; step < iterations; ++step) {
            var force = Object.create(null)
            for (i = 0; i < n; ++i) force[members[i]] = {x: 0, y: 0}
            for (i = 0; i < n; ++i) {
                for (var j = i + 1; j < n; ++j) {
                    var p = local[members[i]], other = local[members[j]]
                    var dx = other.x - p.x, dy = other.y - p.y
                    var distance = Math.max(.01, Math.sqrt(dx * dx + dy * dy))
                    var connected = adjacent[members[i]].indexOf(members[j]) >= 0
                    var push = (connected ? 1800 : 16000) / (distance * distance)
                    force[members[i]].x -= dx / distance * push; force[members[i]].y -= dy / distance * push
                    force[members[j]].x += dx / distance * push; force[members[j]].y += dy / distance * push
                }
            }
            for (k = 0; k < edges.length; ++k) {
                a = edges[k][0]; b = edges[k][1]
                p = local[a]; other = local[b]
                dx = other.x - p.x; dy = other.y - p.y
                distance = Math.max(.01, Math.sqrt(dx * dx + dy * dy))
                // Only this directly connected pair gains attraction. Saturation
                // keeps many roles from collapsing nodes or shrinking their radii.
                var pairKey = JSON.stringify([a, b].sort())
                var count = Object.keys(roles[pairKey]).length
                var gap = (24 + 46 / Math.max(1, count)) * 1.25
                var stiffness = .08 * (1 + 8 * Math.log(Math.max(1, count)))
                var pull = (distance - p.r - other.r - gap) * stiffness
                force[a].x += dx / distance * pull; force[a].y += dy / distance * pull
                force[b].x -= dx / distance * pull; force[b].y -= dy / distance * pull
            }
            var cap = 5 * (1 - step / iterations) + .3
            for (i = 0; i < n; ++i) {
                var f = force[members[i]], length = Math.sqrt(f.x * f.x + f.y * f.y) || 1
                local[members[i]].x += f.x * Math.min(1, cap / length)
                local[members[i]].y += f.y * Math.min(1, cap / length)
            }
            separate(members, local)
        }
        // A final geometric pass preserves the supplied node collision radii.
        for (step = 0; step < 24; ++step) separate(members, local)
        var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
        for (i = 0; i < n; ++i) {
            p = local[members[i]]
            minX = Math.min(minX, p.x - p.r); maxX = Math.max(maxX, p.x + p.r)
            minY = Math.min(minY, p.y - p.r); maxY = Math.max(maxY, p.y + p.r)
        }
        var box = {ids: members, points: local, minX: minX, minY: minY,
                   width: maxX - minX, height: maxY - minY}
        boxes.push(box); area += (box.width + 75) * (box.height + 75); widest = Math.max(widest, box.width)
    }
    // Disconnected components get separate boxes, so unrelated people do not
    // crowd between members of one connected group. Only the camera fits it.
    var targetWidth = Math.max(widest, Math.sqrt(area * Math.max(.35, width / Math.max(1, height))))
    var x = 0, y = 0, rowHeight = 0, totalWidth = 0
    for (c = 0; c < boxes.length; ++c) {
        box = boxes[c]
        if (x > 0 && x + box.width > targetWidth) { x = 0; y += rowHeight + 75; rowHeight = 0 }
        for (i = 0; i < box.ids.length; ++i) {
            id = box.ids[i]; p = box.points[id]
            positions[id] = {x: x + p.x - box.minX, y: y + p.y - box.minY, r: p.r}
        }
        totalWidth = Math.max(totalWidth, x + box.width); rowHeight = Math.max(rowHeight, box.height)
        x += box.width + 75
    }
    var totalHeight = boxes.length ? y + rowHeight : 0
    for (i = 0; i < ids.length; ++i) {
        positions[ids[i]].x -= totalWidth / 2; positions[ids[i]].y -= totalHeight / 2
    }
    return {positions: positions, components: components, bounds: {width: totalWidth, height: totalHeight},
            fitScale: Math.max(.001, Math.min(1, Math.max(1, width - 48) / Math.max(1, totalWidth),
                                                 Math.max(1, height - 48) / Math.max(1, totalHeight)))}
}

function separate(ids, points) {
    for (var i = 0; i < ids.length; ++i) {
        for (var j = i + 1; j < ids.length; ++j) {
            var a = points[ids[i]], b = points[ids[j]], dx = b.x - a.x, dy = b.y - a.y
            var distance = Math.sqrt(dx * dx + dy * dy)
            var minimum = a.r + b.r + 18
            if (distance >= minimum) continue
            if (distance < .001) { dx = 1; dy = .5; distance = Math.sqrt(1.25) }
            var shift = (minimum - distance) / 2
            a.x -= dx / distance * shift; a.y -= dy / distance * shift
            b.x += dx / distance * shift; b.y += dy / distance * shift
        }
    }
}


function labelWidth(edge) {
    var text = String(edge.kind || "") + (edge.state ? "（" + edge.state + "）" : "")
    var size = 0
    for (var i = 0; i < text.length; ++i) size += text.charCodeAt(i) > 255 ? 11 : 6.5
    return size
}

function rectangleHitsCircle(rect, point, padding) {
    var dx = Math.max(rect.x - point.x, 0, point.x - rect.x - rect.w)
    var dy = Math.max(rect.y - point.y, 0, point.y - rect.y - rect.h)
    return dx * dx + dy * dy < Math.pow(point.r + padding, 2)
}

function rectanglesOverlap(a, b, gap) {
    return a.x < b.x + b.w + gap && a.x + a.w + gap > b.x
        && a.y < b.y + b.h + gap && a.y + a.h + gap > b.y
}

function curvePoint(route, t) {
    var u = 1 - t
    return {x: u*u*route.start.x + 2*u*t*route.cx + t*t*route.end.x,
            y: u*u*route.start.y + 2*u*t*route.cy + t*t*route.end.y}
}

function circlePoint(node, target, padding) {
    var dx = target.x - node.x, dy = target.y - node.y
    var length = Math.sqrt(dx*dx + dy*dy) || 1
    return {x: node.x + dx / length * (node.r + padding), y: node.y + dy / length * (node.r + padding)}
}

// Base routes are stable world coordinates. Screen-sized labels can be
// remeasured with placeLabels without changing curves, nodes or the camera.
function routeEdges(positions, links, sizes, width, height) {
    var indexed = links.map(function(edge, i) { return {edge: edge, index: i, size: sizes[i] || {width: labelWidth(edge), height: 18}} })
    function edgeKey(item) { var e = item.edge; return JSON.stringify([e.from, e.to, e.kind || "", e.state || "", e.slot || 0, e.both === true]) }
    indexed.sort(function(a, b) { var ak = edgeKey(a), bk = edgeKey(b); return ak < bk ? -1 : (ak > bk ? 1 : 0) })
    var groups = Object.create(null), usedBows = Object.create(null), routes = []
    for (var i = 0; i < indexed.length; ++i) {
        var e = indexed[i].edge, pair = JSON.stringify([e.from, e.to].sort())
        if (!groups[pair]) { groups[pair] = []; usedBows[pair] = [] }
        groups[pair].push(indexed[i])
    }
    for (i = 0; i < indexed.length; ++i) {
        var item = indexed[i], edge = item.edge, a = positions[edge.from], b = positions[edge.to]
        if (!a || !b) continue
        pair = JSON.stringify([edge.from, edge.to].sort())
        var siblings = groups[pair], lane = siblings.indexOf(item) - (siblings.length - 1) / 2
        var dx = b.x - a.x, dy = b.y - a.y, length = Math.sqrt(dx*dx + dy*dy) || 1
        var nx = -dy / length, ny = dx / length
        if (edge.from > edge.to) { nx = -nx; ny = -ny }
        var widest = 0, tallest = 18
        for (var g = 0; g < siblings.length; ++g) { widest = Math.max(widest, siblings[g].size.width); tallest = Math.max(tallest, siblings[g].size.height) }
        var laneGap = 2 * (tallest + 18) + Math.min(48, widest * .4)
        var route = null
        // A finite graph always has a free outer curve. Search both directions
        // rather than retaining a curve that knowingly crosses another node.
        for (var ring = 0; route === null; ++ring) {
            var bends = ring === 0 ? [lane * laneGap] : [(lane + ring) * laneGap, (lane - ring) * laneGap]
            for (var candidate = 0; candidate < bends.length && route === null; ++candidate) {
                if (usedBows[pair].some(function(bow) { return Math.abs(bow-bends[candidate]) < laneGap*.55 })) continue
                var control = {x: (a.x+b.x)/2 + nx*bends[candidate], y: (a.y+b.y)/2 + ny*bends[candidate]}
                var trial = {edge: edge, cx: control.x, cy: control.y,
                             start: circlePoint(a, control, 2), end: circlePoint(b, control, 6)}
                var clear = true
                for (var t = .08; t < .94 && clear; t += .07) {
                    var point = curvePoint(trial, t)
                    for (var nodeId in positions) {
                        if (nodeId === edge.from || nodeId === edge.to) continue
                        var node = positions[nodeId], px = point.x-node.x, py = point.y-node.y
                        if (px*px+py*py < Math.pow(node.r+7,2)) { clear = false; break }
                    }
                }
                if (clear) { route = trial; usedBows[pair].push(bends[candidate]) }
            }
        }
        route.edge_index = item.index
        route.edge_key = edgeKey(item)
        routes.push(route)
    }
    routes = placeLabels(positions, routes, sizes)
    var minX = 0, minY = 0, maxX = 0, maxY = 0
    for (var id in positions) { var p = positions[id]; minX=Math.min(minX,p.x-p.r);maxX=Math.max(maxX,p.x+p.r);minY=Math.min(minY,p.y-p.r);maxY=Math.max(maxY,p.y+p.r) }
    for (i = 0; i < routes.length; ++i) {
        var r = routes[i], box = r.label
        minX=Math.min(minX,box.x,r.cx);maxX=Math.max(maxX,box.x+box.w,r.cx)
        minY=Math.min(minY,box.y,r.cy);maxY=Math.max(maxY,box.y+box.h,r.cy)
    }
    var extentX = Math.max(Math.abs(minX),Math.abs(maxX)), extentY = Math.max(Math.abs(minY),Math.abs(maxY))
    return {edges: routes, bounds: {minX:minX,minY:minY,maxX:maxX,maxY:maxY},
            fitScale: Math.min(1,Math.max(1,width-40)/Math.max(1,2*extentX),Math.max(1,height-40)/Math.max(1,2*extentY))}
}

// Segment/rectangle clipping tests the complete padded rectangle, rather than
// just whether a sampled curve point happens to land inside the text.
function segmentHitsBox(a, b, box, padding) {
    var lo = 0, hi = 1, dx = b.x-a.x, dy = b.y-a.y
    var p = [-dx, dx, -dy, dy]
    var q = [a.x-box.x+padding, box.x+box.w+padding-a.x,
             a.y-box.y+padding, box.y+box.h+padding-a.y]
    for (var i=0; i<4; ++i) {
        if (Math.abs(p[i]) < .00001) { if (q[i] < 0) return false; continue }
        var t = q[i]/p[i]
        if (p[i] < 0) lo = Math.max(lo,t); else hi = Math.min(hi,t)
        if (lo > hi) return false
    }
    return true
}

function curveHitsBox(route, box, padding) {
    var before = curvePoint(route,0)
    for (var i=1; i<=64; ++i) {
        var next = curvePoint(route,i/64)
        if (segmentHitsBox(before,next,box,padding)) return true
        before = next
    }
    return false
}

// Routes and camera remain fixed. Only text rectangles are placed again when
// their measured screen font changes. Each label clears every unrelated edge.
function placeLabels(positions, routes, sizes) {
    var boxes = [], result = []
    for (var i=0; i<routes.length; ++i) {
        var route = routes[i], size = sizes[route.edge_index] || {width:labelWidth(route.edge),height:18}
        var w=size.width+8, h=size.height+4, spot=null
        function clear(rect) {
            for (var id in positions) if (rectangleHitsCircle(rect,positions[id],8)) return false
            for (var j=0; j<boxes.length; ++j) if (rectanglesOverlap(rect,boxes[j],7)) return false
            for (var k=0; k<routes.length; ++k)
                if (k !== i && curveHitsBox(routes[k],rect,6)) return false
            return true
        }
        // Prefer a label on its own arc, searching the full middle span before
        // considering a short perpendicular leader. No global-grid detachment.
        var samples=[.5,.45,.55,.4,.6,.35,.65,.3,.7,.25,.75,.2,.8]
        for (var ring=0; spot===null; ++ring) {
            for (var sample=0; sample<samples.length && spot===null; ++sample) {
                var t=samples[sample], anchor=curvePoint(route,t)
                var dx=2*((1-t)*(route.cx-route.start.x)+t*(route.end.x-route.cx))
                var dy=2*((1-t)*(route.cy-route.start.y)+t*(route.end.y-route.cy))
                var len=Math.sqrt(dx*dx+dy*dy)||1, offsets=ring===0?[0]:[ring*(h+10),-ring*(h+10)]
                for (var side=0; side<offsets.length; ++side) {
                    var x=anchor.x-dy/len*offsets[side], y=anchor.y+dx/len*offsets[side]
                    var rect={x:x-w/2,y:y-h/2,w:w,h:h}
                    if (clear(rect)) { spot={rect:rect,anchor:anchor}; break }
                }
            }
        }
        var box=spot.rect, cx=box.x+w/2, cy=box.y+h/2
        var vx=spot.anchor.x-cx, vy=spot.anchor.y-cy
        var ratio=Math.max(Math.abs(vx)/(w/2),Math.abs(vy)/(h/2))
        var from=ratio>1?{x:cx+vx/ratio,y:cy+vy/ratio}:spot.anchor
        box.anchorX=spot.anchor.x; box.anchorY=spot.anchor.y
        box.text=String(route.edge.kind||"")+(route.edge.state?"（"+route.edge.state+"）":"")
        var copy={}
        for (var key in route) copy[key]=route[key]
        copy.label=box; copy.leader={from:from,to:spot.anchor}
        boxes.push(box); result.push(copy)
    }
    return result
}
