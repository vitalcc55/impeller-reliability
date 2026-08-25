export interface ReportRenderer<TSnapshot> {
  render(snapshot: Readonly<TSnapshot>): Promise<string>;
}
