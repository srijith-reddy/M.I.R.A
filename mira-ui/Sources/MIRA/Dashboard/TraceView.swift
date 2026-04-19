import SwiftUI

/// Per-turn trace — every event emitted during one turn, with timestamps
/// and serialized fields. Mirrors the `/api/events?turn_id=…` view from
/// the Flask dashboard but with proper syntax highlighting instead of
/// `<pre>` text.
struct TraceView: View {
    @EnvironmentObject private var client: DashboardClient

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Trace")
                        .font(Typography.dashH1)
                        .foregroundStyle(Palette.text)
                    if let id = client.selectedTurnID {
                        Text(id)
                            .font(.system(size: 11, weight: .medium, design: .monospaced))
                            .foregroundStyle(Palette.accentB)
                    } else {
                        Text("select a turn from Overview or Turns")
                            .font(Typography.cardMeta)
                            .foregroundStyle(Palette.muted)
                            .textCase(.uppercase)
                            .tracking(1)
                    }
                }
                Spacer()
                if !client.trace.isEmpty {
                    Text("\(client.trace.count) events")
                        .font(Typography.cardMeta)
                        .foregroundStyle(Palette.muted)
                        .textCase(.uppercase)
                        .tracking(1)
                }
            }
            .padding(24)

            Divider().background(Palette.hairline)

            ScrollView {
                LazyVStack(spacing: 1) {
                    ForEach(client.trace) { ev in
                        TraceEventRow(event: ev)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 8)
            }
        }
    }
}

struct TraceEventRow: View {
    let event: TraceEvent
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 12) {
                Text(fmtTime(event.ts))
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(Palette.muted)
                    .frame(width: 90, alignment: .leading)

                Text(event.event)
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(eventColor)
                    .frame(width: 220, alignment: .leading)

                Text(preview)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Palette.muted)
                    .lineLimit(expanded ? nil : 1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: expanded ? "chevron.down" : "chevron.right")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(Palette.dim)
            }
            if expanded {
                Text(prettyFields)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Palette.text)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.black.opacity(0.35))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .strokeBorder(Palette.hairline, lineWidth: 1)
                    )
                    .textSelection(.enabled)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(expanded ? Color.white.opacity(0.04) : .clear)
        )
        .contentShape(RoundedRectangle(cornerRadius: 8))
        .onTapGesture { expanded.toggle() }
    }

    private var preview: String {
        guard let fields = event.fields, !fields.isEmpty else { return "" }
        let pieces: [String] = fields.prefix(4).map { k, v in
            "\(k)=\(stringify(v.value, max: 40))"
        }
        return pieces.joined(separator: "  ")
    }

    private var prettyFields: String {
        guard let fields = event.fields else { return "{}" }
        let dict = fields.mapValues { stringify($0.value, max: 10_000) }
        let sorted = dict.sorted { $0.key < $1.key }
        return sorted.map { "\($0.key): \($0.value)" }.joined(separator: "\n")
    }

    private var eventColor: Color {
        if event.event.contains("error") { return Palette.danger }
        if event.event.hasPrefix("ui.")       { return Palette.accentC }
        if event.event.hasPrefix("supervisor") { return Palette.accentA }
        if event.event.hasPrefix("tool.")     { return Palette.accentB }
        if event.event.hasPrefix("llm.")      { return Palette.warm }
        return Palette.muted
    }
}

private func stringify(_ v: Any, max: Int) -> String {
    let s: String
    switch v {
    case let s0 as String:
        s = "\"\(s0)\""
    case let d as Double:
        s = String(d)
    case let i as Int:
        s = String(i)
    case let b as Bool:
        s = String(b)
    case let arr as [Any]:
        s = "[\(arr.count)]"
    case let dict as [String: Any]:
        s = "{\(dict.count)}"
    default:
        s = "null"
    }
    if s.count <= max { return s }
    return String(s.prefix(max)) + "…"
}
