#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "remote.h"
static void output(IRDATA *ir) {
  IRDATA_LAN wrap;
  memset(&wrap,0,sizeof(wrap));
  wrap.netcommand=COMMAND_LAN;
  wrap.result_port=31000;
  ir->checksumme=0;
  for(int i=2;i<ir->len;i++) ir->checksumme+=((uint8_t*)ir)[i];
  memcpy(&wrap.ir_data,ir,ir->len);
  for(int i=0;i<68+ir->len;i++) printf("%02x",((uint8_t*)&wrap)[i]);
  puts("");
}
int main(void) {
  IRDATA ir;
  memset(&ir,0,sizeof(ir));
  ir.len=8; ir.command=HOST_VERSION; ir.address=VERSION_MAGIC_5;
  ir.target_mask=VERSION_MAGIC_2; ir.ir_length=VERSION_MAGIC_3;
  ir.transmit_freq=VERSION_MAGIC_4;
  output(&ir);
  memset(&ir,0,sizeof(ir)); ir.len=7; ir.command=HOST_LEARNIR; output(&ir);
  memset(&ir,0,sizeof(ir)); ir.len=7; ir.command=HOST_LEARNIRRAW; output(&ir);
  memset(&ir,0,sizeof(ir)); ir.len=5; output(&ir);
  printf("%zu %zu %zu %zu\n",sizeof(IRDATA_LAN)-sizeof(IRDATA),
    (size_t)((uint8_t*)ir.data-(uint8_t*)&ir),
    (size_t)((uint8_t*)&ir.time_cnt-(uint8_t*)&ir),sizeof(GETVERSION_LAN_EX3));
  return 0;
}
