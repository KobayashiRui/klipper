#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // __visible
#include "itersolve.h" // struct stepper_kinematics
#include "pyhelper.h" // errorf
#include "trapq.h" // move_get_coord

static double
corexy_ac_stepper_a_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return move_get_coord(m, move_time).u;

}

static double
corexy_ac_stepper_c_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return move_get_coord(m, move_time).w;
}


struct stepper_kinematics * __visible
corexy_ac_stepper_alloc(char axis)
{
    struct stepper_kinematics *sk = malloc(sizeof(*sk));
    memset(sk, 0, sizeof(*sk));
    if (axis == 'a') {
        sk->calc_position_cb = corexy_ac_stepper_a_calc_position;
        sk->active_flags = AF_U;
    } else if (axis == 'c') {
        sk->calc_position_cb = corexy_ac_stepper_c_calc_position;
        sk->active_flags = AF_W;
    }
    return sk;
}