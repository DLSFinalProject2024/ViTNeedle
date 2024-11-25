from typing import List
from needle.autograd import Tensor
import needle.backend_ndarray.ndarray as ndarray
from needle import ops
import needle.init as init
import numpy as np
from .nn_sequence import Embedding
from .nn_basic import (
    Parameter, 
    Module, 
    ReLU,
    Tanh,
    Dropout,
    LayerNorm1d,
    Linear,
    Sequential,
    Residual
)

from .nn_conv import (
    ConvGp
)

def exists(val):
    return val is not None

def default(val, d):
    return val if exists(val) else d

def divisible_by(numer, denom):
    return (numer % denom) == 0

def create_grid_like(t, dim = 0):
    b, c, h, w = t.shape
    device = t.device  # Get the device of the input tensor
    assert c == 2

    # Create the grid using NumPy
    grid = np.stack(np.meshgrid(np.arange(w), np.arange(h), indexing='xy'), axis=dim)
    grid = np.reshape(grid, (1, c, h, w))
    grid = np.broadcast_to(grid, (b, c, h, w))

    # Convert to PyTorch tensor and move to the same device and dtype as `t`
    grid_tensor = Tensor(grid, device=device, dtype=t.dtype, requires_grad=False)
    return grid_tensor

def normalize_grid(grid, dim = 1, out_dim = -1):
    # normalizes a grid to range from -1 to 1
    b, c, h, w = grid.shape
    device, dtype = grid.device, grid.dtype

    grid_h, grid_w = ops.split(grid, axis = dim)

    grid_h = 2.0 * grid_h / max(h - 1, 1) - 1.0
    grid_w = 2.0 * grid_w / max(w - 1, 1) - 1.0

    return ops.stack([grid_h, grid_w], axis=out_dim)    

class Scale(Module):
    def __init__(self, scale):
        super().__init__()
        self.scale = scale

    def forward(self, x):
        return x * self.scale

# Only support tanh approximation
class GELU(Module):
    def forward(self, X: Tensor) -> Tensor:
        x_cubed = ops.power_scalar(X, 3)  # x^3 = x * x * x
        scaled_x_cubed = ops.mul_scalar(x_cubed, 0.044715)
        z = ops.add(X, scaled_x_cubed)  # z = x + 0.044715 * x^3
        z_scaled = ops.mul_scalar(z, np.sqrt(2/np.pi))
        tanh_z = ops.tanh(z_scaled)  # Tanh(sqrt(2/pi)*(x+0.044715*x^3))
        w = ops.add_scalar(tanh_z, 1)
        gelu = ops.mul_scalar(ops.multiply(X, w), 0.5)  # GELU(x) = 0.5 * x * (1 + tanh(...))
        return gelu        

class DeformableAttention(Module):
    """
    The deformable attention module.
    """
    def __init__(
        self,
        *,
        dim=32,
        dim_head = 4,
        heads = 8,
        dropout = 0.,
        downsample_factor = 4,
        offset_scale = None,
        offset_groups = None,
        offset_kernel_size = 5,
        group_queries = True,
        group_key_values = True,
        to_q_bias = False,
        to_k_bias = False,
        to_v_bias = False,
        to_out_bias = False,
        device = None,
        dtype = "float32",
    ):

        super().__init__()

        self.device = device
        self.dtype = dtype

        offset_scale = default(offset_scale, downsample_factor)
        assert offset_kernel_size >= downsample_factor, 'offset kernel size must be greater than or equal to the downsample factor'
        # Only to make sure padding = (offset_kernel_size - downsample_factor)/2 is integer.
        # Since I always set 'same' padding, padding = (offset_kernel_size -1)/2, I do not need this assertion.
        #assert divisible_by(offset_kernel_size - downsample_factor, 2)

        offset_groups = default(offset_groups, heads)
        assert divisible_by(heads, offset_groups)

        self.dim = dim
        self.inner_dim = dim_head * heads
        self.scale = dim_head ** -0.5
        self.heads = heads
        self.offset_groups = offset_groups

        self.offset_dims = self.inner_dim // offset_groups

        self.downsample_factor = downsample_factor
        
        self.to_q_bias = to_q_bias
        self.to_k_bias = to_k_bias
        self.to_v_bias = to_v_bias
        self.to_out_bias = to_out_bias

        self.to_offsets = Sequential(
            ConvGp(self.offset_dims, self.offset_dims, offset_kernel_size, groups = self.offset_dims, stride = downsample_factor, bias=True, device=self.device, dtype=self.dtype),
            GELU(),
            ConvGp(self.offset_dims, 2, 1, bias = False),
            Tanh(),
            Scale(offset_scale)
        )

        #self.rel_pos_bias = CPB(self.dim // 4, offset_groups = offset_groups, heads = heads, depth = 2)
        self.dropout = Dropout(dropout)
        self.to_q = ConvGp(self.dim, self.inner_dim, 1, groups = offset_groups if group_queries else 1, bias = self.to_q_bias, device=self.device, dtype=self.dtype)
        self.to_k = ConvGp(self.dim, self.inner_dim, 1, groups = offset_groups if group_key_values else 1, bias = self.to_k_bias, device=self.device, dtype=self.dtype)
        self.to_v = ConvGp(self.dim, self.inner_dim, 1, groups = offset_groups if group_key_values else 1, bias = self.to_v_bias, device=self.device, dtype=self.dtype)
        self.to_out = ConvGp(self.inner_dim, self.dim, 1, bias=self.to_out_bias, device=self.device, dtype=self.dtype)

    def softmax(self, logit):
        """
        The softmax function; 
        """
        max_val = Tensor(
            logit.realize_cached_data().max(axis=3),
            device=logit.device,
            dtype=logit.dtype,
            requires_grad=False
        )

        max_val = max_val.reshape((*logit.shape[:-1], 1))
        max_val = max_val.broadcast_to(logit.shape)

        probs = ops.exp(logit - max_val)

        denom = probs.sum(axes=3)
        denom = denom.reshape((*logit.shape[:-1], 1))
        denom = denom.broadcast_to(logit.shape)

        return probs / denom

    def forward(
        self,
        x,
        return_vgrid=False,
        return_norm_vgrid=False
    ):
        """
        The forward function of the Deformable Attention function.
        Input: x with shape (batch_size, in_channels, height, width), NCHW
        Output: z with shape (batch_size, in_channels, height, width), NCHW
        """

        heads, Bin, Cin, Hin, Win, downsample_factor, device = self.heads, *x.shape, self.downsample_factor, x.device

        # queries
        q = self.to_q(x)
        _, _, h_q, w_q = q.shape

        # reshape queries into groups
        grouped_queries = q.reshape((Bin*self.offset_groups, self.offset_dims, h_q, w_q))

        # pass groups of queries into offset network
        offsets = self.to_offsets(grouped_queries)

        grid = create_grid_like(offsets)
        vgrid = grid+offsets
        vgrid_scaled = normalize_grid(vgrid, dim=1, out_dim=3)

        if return_norm_vgrid:
            return q, grouped_queries, offsets, vgrid_scaled

        return q, grouped_queries, offsets



