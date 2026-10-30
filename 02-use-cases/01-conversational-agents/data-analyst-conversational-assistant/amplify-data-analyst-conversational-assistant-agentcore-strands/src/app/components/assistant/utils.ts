// ─── Assistant Utility Functions ─────────────────────────────────────────────

export const extractBetweenTags = (str: string, tag: string): string => {
  const startTag = `<${tag}>`;
  const endTag = `</${tag}>`;
  const startIndex = str.indexOf(startTag);
  const endIndex = str.indexOf(endTag, startIndex);
  if (startIndex === -1 || endIndex === -1) return '';
  return str.slice(startIndex + startTag.length, endIndex);
};

export const removeCharFromStartAndEnd = (str: string, char: string): string => {
  while (str.startsWith(char)) str = str.substring(1);
  while (str.endsWith(char)) str = str.substring(0, str.length - 1);
  return str;
};

export const handleFormatter = (obj: Record<string, unknown>): Record<string, unknown> => {
  if (typeof obj === 'object' && obj !== null) {
    for (const key in obj) {
      const val = obj[key];
      if (typeof val === 'string') {
        if (key === 'formatter' && (val === '%' || val.startsWith('$'))) {
          // Simple format strings — skip conversion
        } else if (key === 'formatter') {
          // Convert function string to actual function
          obj[key] = new Function('return ' + val)();
        }
      } else if (typeof val === 'object' && val !== null) {
        handleFormatter(val as Record<string, unknown>);
      }
    }
  }
  return obj;
};

/** The chart generation prompt template — same as the original React app */
export const CHART_PROMPT = `
Create detailed ApexCharts.js configurations based on the information provided to support the answer. Focus on meaningful data analysis and visually appealing charts.

Input Data:

<information>
    <summary>
        <<answer>>
    </summary>
    <data_sources>
        <<data_sources>>
    </data_sources>
</information>

The following is the only required output format for a Chart:

<has_chart>1</has_chart>
<chart_type>[bar/line/pie/etc]</chart_type>
<chart_configuration>[JSON validate format with series and options]</chart_configuration>
<caption>[Insightful analysis about the data chart in 20-40 words]</caption>

If you do not have a chart configuration, use only the following output format:

<has_chart>0</has_chart>
<rationale>[The reason to do not generate a chart configuration, max 12 words]</rationale>

- Provide the caption and chart information in the same language as the summary information.

Chart Requirements:

   - Provide only 1 chart configuration
   - Use the appropriate chart type based on the data
   - Each chart must include:
      - Complete series and options configuration

ApexCharts Technical Specifications:

    - Provide the formatter function value as a string in double quotes
    - Use standard ApexCharts.js for React.js syntax
    - Format all property names and string values with double quotes
    - Include appropriate titles, subtitles and axis labels
    - Configure for light mode viewing
    - Use default text format styles
    - Format decimal values to two places using formatter functions
    - Use simple JavaScript functions (no moment.js)

ApexCharts Rules to Avoid Known Errors:

   - Do not use Multiple Y Axis for bars, those are not supported.
   - In JSON format, avoid the error: raise JSONDecodeError("Expecting value", s, err.value) from None
   - Do not use 'undefined' values

Example Chart Configurations:

<ChartExamples>
  <Chart description="Line Basic">
    <type>line</type>
    <configuration>
{
   "series":[{"name":"Desktops","data":[10,41,35,51,49,62,69,91,148]}],
   "options":{
      "chart":{"height":420,"type":"line","zoom":{"enabled":false}},
      "dataLabels":{"enabled":false},
      "stroke":{"curve":"straight"},
      "title":{"text":"Product Trends by Month","align":"left"},
      "grid":{"row":{"colors":["#f3f3f3","transparent"],"opacity":0.5}},
      "xaxis":{"categories":["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep"]}
   }
}
    </configuration>
  </Chart>

  <Chart description="Bar Funnel">
    <type>bar</type>
    <configuration>
{
   "series":[{"name":"Funnel Series","data":[1380,1100,990,880,740,548,330,200]}],
   "options":{
      "chart":{"type":"bar","height":420,"dropShadow":{"enabled":true}},
      "plotOptions":{"bar":{"borderRadius":0,"horizontal":true,"barHeight":"80%","isFunnel":true}},
      "dataLabels":{"enabled":true,"formatter":"function (val, opt) { return opt.w.globals.labels[opt.dataPointIndex] + ':  ' + val }","dropShadow":{"enabled":true}},
      "title":{"text":"Recruitment Funnel","align":"middle"},
      "xaxis":{"categories":["Sourced","Screened","Assessed","HR Interview","Technical","Verify","Offered","Hired"]},
      "legend":{"show":false}
   }
}
    </configuration>
  </Chart>

  <Chart description="Bar Basic">
    <type>bar</type>
    <configuration>
{
   "series":[{"data":[400,430,448,470,540,580,690,1100,1200,1380]}],
   "options":{
      "chart":{"type":"bar","height":420},
      "plotOptions":{"bar":{"borderRadius":4,"borderRadiusApplication":"end","horizontal":true}},
      "dataLabels":{"enabled":false},
      "xaxis":{"categories":["South Korea","Canada","United Kingdom","Netherlands","Italy","France","Japan","United States","China","Germany"]}
   }
}
    </configuration>
  </Chart>

  <Chart description="Simple Pie">
    <type>pie</type>
    <configuration>
{
  "series": [2077, 1036.75, 384.99, 277.49],
  "options": {
    "chart": {"type": "pie", "height": 420},
    "labels": ["North America", "Europe", "Other Regions", "Japan"],
    "title": {"text": "Video Game Sales Distribution by Region (2000-2010)", "align": "center"},
    "subtitle": {"text": "Total Global Sales: 3,779.72 million units", "align": "center"},
    "dataLabels": {"enabled": true, "formatter": "function (val, opt) { return opt.w.config.labels[opt.seriesIndex] + ': ' + val.toFixed(2) + '%' }"},
    "legend": {"position": "bottom"},
    "colors": ["#008FFB", "#00E396", "#FEB019", "#FF4560"]
  }
}
    </configuration>
  </Chart>
</ChartExamples>`;
